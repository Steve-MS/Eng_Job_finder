"""Adzuna Jobs API adapter for the UK contract PM market.

Overview
--------
Adzuna is a job-search aggregator whose free-tier API provides structured JSON
access to vacancies drawn from Totaljobs, CWJobs, Indeed and many other UK
boards.  Using the partner API is the ToS-clean path to data from Akamai-
blocked StepStone-family sites that cannot be scraped directly.

Endpoint
--------
UK search:  https://api.adzuna.com/v1/api/jobs/gb/search/{page}
            page is 1-indexed; each response contains up to results_per_page
            results plus a ``count`` field (total matching vacancies).

Authentication
--------------
Two query parameters are required on every request:
    app_id   — 8-character application identifier (ADZUNA_APP_ID in .env)
    app_key  — 32-character secret key           (ADZUNA_APP_KEY in .env)
These are passed as plain query params (not in the Authorization header).
Both values are treated as secrets; they MUST NOT appear in fixtures or logs.

Rate limits (free tier)
-----------------------
  50 requests/minute, 1 000 requests/day.
  At results_per_page=50 and 10-page cap we consume ≤10 requests per run.
  The default _PAGE_DELAY_SECONDS (1.2 s) keeps us well inside the minute
  ceiling.  The orchestrator crawl_delay of 1 s provides an additional
  between-source buffer.

Pagination
----------
  Pages are 1-indexed.  The adapter loops from page 1 up to a calculated
  max_pages ceiling (min(safety_cap // results_per_page, _MAX_PAGES)).
  An early-exit fires when the returned result count is less than
  results_per_page — that is the final page.

Error handling
--------------
  429 Too Many Requests → sleep _RETRY_SLEEP_429 seconds, retry once.
  5xx server errors     → up to _MAX_5XX_RETRIES retries with _RETRY_SLEEP_5XX
                          sleep between attempts.
  401 / 403             → log "credentials invalid or revoked", return [].
  Any remaining exception inside fetch() → log WARNING, return [] or partial.
  The adapter NEVER raises; the orchestrator always continues to the next
  source.

Salary semantics (⚠ important for Ada's extractor)
---------------------------------------------------
Adzuna exposes salary_min and salary_max as numeric fields.  For contract
roles these are typically *annualised salary equivalents* inferred from the
job description or the hiring agency's posting — NOT day rates.  Adzuna's
own documentation acknowledges that day rates are converted to annual figures
using a standard multiplier before being indexed.

The adapter faithfully forwards these values as a human-readable string
(``"£50,000–£70,000"``).  When Adzuna's ML model has predicted the salary
rather than extracting it verbatim from the posting, salary_is_predicted=="1"
and the string carries a ``(predicted)`` suffix.

Ada's extractor MUST treat salary_raw from this adapter as an annual
equivalent and attempt to derive a day-rate estimate via its own heuristics.
Do NOT assume salary_raw is a day rate for Adzuna-sourced listings.

Fields mapped to RawListing
----------------------------
  source              = "adzuna"
  source_listing_id   = str(result["id"])
  title               = result["title"]
  employer            = result["company"]["display_name"]  (safe get)
  location_raw        = result["location"]["display_name"]
  url                 = result["redirect_url"]
  posted_at           = ISO-8601 UTC datetime from result["created"]
  salary_raw          = built from salary_min / salary_max / salary_is_predicted
  description_raw     = result.get("description")
  contract_type_raw   = result.get("contract_type")   → "contract" expected
  metadata            = salary_min, salary_max, salary_is_predicted,
                        contract_time, category_label, latitude, longitude
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.adzuna")

ADZUNA_API_BASE = "https://api.adzuna.com/v1/api/jobs"
_DEFAULT_KEYWORDS = "project manager mechanical engineering"
_DEFAULT_COUNTRY = "gb"
_DEFAULT_RESULTS_PER_PAGE = 50
_DEFAULT_SAFETY_CAP = 500
_MAX_PAGES = 10           # hard cap regardless of safety_cap arithmetic
_PAGE_DELAY_SECONDS = 1.2  # stays well inside 50 req/min free-tier ceiling
_REQUEST_TIMEOUT = 30.0
_MAX_5XX_RETRIES = 2
_RETRY_SLEEP_5XX = 5.0
_RETRY_SLEEP_429 = 60.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_adzuna_date(date_str: str | None) -> datetime | None:
    """Parse Adzuna's ISO-8601 UTC date string to a UTC-aware datetime.

    Adzuna returns "created" as ``"2026-06-09T19:56:23Z"``.  Additional
    formats are attempted defensively in case of future schema drift.
    """
    if not date_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("adzuna: could not parse date string: %r", date_str)
    return None


def _build_salary_raw(result: dict[str, Any]) -> str | None:
    """Build a human-readable salary string from Adzuna numeric salary fields.

    ⚠ Adzuna salary values for contract roles are typically annualised
    equivalents, NOT day rates.  Ada's extractor must handle conversion.

    Returns None when both salary_min and salary_max are absent.
    Appends "(predicted)" when salary_is_predicted == "1".
    """
    sal_min = result.get("salary_min")
    sal_max = result.get("salary_max")
    predicted = result.get("salary_is_predicted") == "1"

    if sal_min is None and sal_max is None:
        return None

    if sal_min is not None and sal_max is not None and sal_min != sal_max:
        base = f"£{sal_min:,.0f}–£{sal_max:,.0f}"
    elif sal_min is not None:
        base = f"£{sal_min:,.0f}"
    else:
        base = f"£{sal_max:,.0f}"  # type: ignore[arg-type]

    return f"{base} (predicted)" if predicted else base


def _parse_page(results: list[dict[str, Any]]) -> list[RawListing]:
    """Parse a list of Adzuna API result dicts into RawListings.

    Extracted as a standalone function so fixture-based unit tests can call
    it directly without making HTTP requests.  Skips individual malformed
    results with a WARNING; never raises.
    """
    listings: list[RawListing] = []
    for result in results:
        try:
            listing_id = str(result.get("id", ""))
            title = result.get("title", "")
            if not title:
                logger.warning("adzuna: skipping result with no title (id=%s)", listing_id)
                continue

            company = result.get("company") or {}
            employer = company.get("display_name") if isinstance(company, dict) else None

            location = result.get("location") or {}
            location_raw = (
                location.get("display_name") if isinstance(location, dict) else None
            )

            category = result.get("category") or {}
            category_label = (
                category.get("label") if isinstance(category, dict) else None
            )

            listings.append(
                RawListing(
                    source="adzuna",
                    source_listing_id=listing_id,
                    url=result.get("redirect_url", ""),
                    title=title,
                    employer=employer,
                    agency=None,
                    location_raw=location_raw,
                    description_raw=result.get("description"),
                    posted_at=_parse_adzuna_date(result.get("created")),
                    salary_raw=_build_salary_raw(result),
                    contract_type_raw=result.get("contract_type"),
                    metadata={
                        "salary_min": result.get("salary_min"),
                        "salary_max": result.get("salary_max"),
                        "salary_is_predicted": result.get("salary_is_predicted"),
                        "salary_annualised": True,  # flag for Ada: NOT a day rate
                        "contract_time": result.get("contract_time"),
                        "category_label": category_label,
                        "latitude": result.get("latitude"),
                        "longitude": result.get("longitude"),
                    },
                )
            )
        except Exception:
            logger.warning(
                "adzuna: skipping malformed result (id=%s).",
                result.get("id", "?"),
                exc_info=True,
            )
    return listings


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class AdzunaAdapter(SourceAdapter):
    """Adapter for the Adzuna Jobs partner API (UK).

    Aggregates listings from Totaljobs, CWJobs, Indeed and many other UK
    boards via the ToS-clean free-tier JSON API.  Credentials (app_id,
    app_key) are loaded from environment variables ADZUNA_APP_ID and
    ADZUNA_APP_KEY.

    Query params (Tommy's v0.2 spec):
      what_or      — OR-query string (preferred, replaces ``what``/``keywords``).
      what_exclude — Exclude terms (noise filter at source).
      location0    — Adzuna location filter (``"UK"`` for nationwide).
      category     — Adzuna category slug (``"engineering-jobs"``).

    Pagination is 1-indexed; the adapter loops until the response result
    count falls below results_per_page or the page cap is reached.
    Returns [] on any unrecoverable error; never raises.
    """

    name = "adzuna"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        crawl_delay: int = 1,
        keywords: str = _DEFAULT_KEYWORDS,
        what_or: str = "",
        what_exclude: str = "",
        location0: str = "UK",
        category: str = "",
        country: str = _DEFAULT_COUNTRY,
        results_per_page: int = _DEFAULT_RESULTS_PER_PAGE,
        safety_cap: int = _DEFAULT_SAFETY_CAP,
        max_pages: int = _MAX_PAGES,
    ) -> None:
        self.app_id = app_id
        self.app_key = app_key
        self.crawl_delay = crawl_delay
        self.keywords = keywords
        self.what_or = what_or
        self.what_exclude = what_exclude
        self.location0 = location0
        self.category = category
        self.country = country
        self.results_per_page = results_per_page
        self.safety_cap = safety_cap
        self.max_pages = max_pages

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch contract PM listings from the Adzuna API, paginating to cap.

        Applies the ``since`` filter client-side on ``posted_at``.
        Sleeps _PAGE_DELAY_SECONDS between page requests to stay inside
        the 50 req/min rate limit.  Handles 429 and 5xx gracefully.
        Returns [] (or partial results) on any unrecoverable error.
        """
        if not self.app_id or not self.app_key:
            logger.warning(
                "ADZUNA_APP_ID or ADZUNA_APP_KEY is not set — skipping Adzuna fetch."
            )
            return []

        max_pages = min(
            max(1, self.safety_cap // self.results_per_page),
            self.max_pages,
        )
        listings: list[RawListing] = []

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                for page_num in range(1, max_pages + 1):
                    page_results = await self._fetch_page(client, page_num)

                    if page_results is None:
                        # Unrecoverable error on this page — stop pagination.
                        break

                    for raw in page_results:
                        if since and raw.posted_at and raw.posted_at < since:
                            continue
                        listings.append(raw)

                    if len(page_results) < self.results_per_page:
                        # Fewer results than requested → last page reached.
                        break

                    if page_num < max_pages:
                        await asyncio.sleep(_PAGE_DELAY_SECONDS)

        except Exception:
            logger.exception(
                "adzuna: unexpected error in fetch() — returning partial results (%d so far).",
                len(listings),
            )
            return listings

        logger.info(
            "adzuna: fetched %d listing(s) (max_pages=%d, since=%s).",
            len(listings),
            max_pages,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        page_num: int,
    ) -> list[RawListing] | None:
        """Fetch one page from the Adzuna API and return parsed RawListings.

        Query strategy (v0.2):
          - ``what_or`` (preferred) uses Adzuna's native OR semantics.
          - Falls back to ``what`` (AND semantics) when ``what_or`` is empty.
          - ``what_exclude`` removes noise at the API level when provided.
          - ``location0`` and ``category`` are added when non-empty.

        Returns:
            list[RawListing] on success (may be empty on last page).
            None on unrecoverable error (caller should stop pagination).
        Handles:
            429 — sleep _RETRY_SLEEP_429 s, retry once; give up on 2nd 429.
            5xx — up to _MAX_5XX_RETRIES retries with _RETRY_SLEEP_5XX sleep.
            401/403 — log credential error, return None.
        """
        url = f"{ADZUNA_API_BASE}/{self.country}/search/{page_num}"
        params: dict[str, Any] = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": self.results_per_page,
            "contract": 1,
            "sort_by": "date",
        }
        # OR query takes precedence over plain keyword search.
        if self.what_or:
            params["what_or"] = self.what_or
        else:
            params["what"] = self.keywords
        if self.what_exclude:
            params["what_exclude"] = self.what_exclude
        if self.location0:
            params["location0"] = self.location0
        if self.category:
            params["category"] = self.category

        attempt = 0
        hit_429 = False

        while True:
            try:
                response = await client.get(url, params=params)

                if response.status_code == 429:
                    if hit_429:
                        logger.warning(
                            "adzuna: second 429 on page %d — giving up.", page_num
                        )
                        return None
                    logger.warning(
                        "adzuna: 429 rate-limited on page %d — sleeping %.0fs then retrying.",
                        page_num,
                        _RETRY_SLEEP_429,
                    )
                    hit_429 = True
                    await asyncio.sleep(_RETRY_SLEEP_429)
                    continue

                if response.status_code in (401, 403):
                    logger.warning(
                        "adzuna: HTTP %d on page %d — "
                        "credentials invalid or revoked — check .env.",
                        response.status_code,
                        page_num,
                    )
                    return None

                if response.status_code >= 500:
                    attempt += 1
                    if attempt <= _MAX_5XX_RETRIES:
                        logger.warning(
                            "adzuna: HTTP %d on page %d (attempt %d/%d) — "
                            "sleeping %.0fs then retrying.",
                            response.status_code,
                            page_num,
                            attempt,
                            _MAX_5XX_RETRIES,
                            _RETRY_SLEEP_5XX,
                        )
                        await asyncio.sleep(_RETRY_SLEEP_5XX)
                        continue
                    logger.warning(
                        "adzuna: HTTP %d on page %d — max retries reached, skipping page.",
                        response.status_code,
                        page_num,
                    )
                    return None

                response.raise_for_status()
                data = response.json()
                results: list[dict[str, Any]] = data.get("results", [])
                logger.debug(
                    "adzuna: page %d → %d result(s) (total count=%s).",
                    page_num,
                    len(results),
                    data.get("count", "?"),
                )
                return _parse_page(results)

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "adzuna: HTTP error on page %d: %s", page_num, exc
                )
                return None
            except Exception:
                logger.exception(
                    "adzuna: request failed on page %d.", page_num
                )
                return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    _app_id = os.environ.get("ADZUNA_APP_ID", "")
    _app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not _app_id or not _app_key:
        print("ADZUNA_APP_ID or ADZUNA_APP_KEY not set in .env — cannot run self-test.")
    else:
        _adapter = AdzunaAdapter(app_id=_app_id, app_key=_app_key)
        _results = asyncio.run(_adapter.fetch())
        print(f"Fetched {len(_results)} listing(s) from Adzuna.")
        if _results:
            first = _results[0]
            print(f"  Sample: '{first.title}' | {first.location_raw} | {first.salary_raw}")
