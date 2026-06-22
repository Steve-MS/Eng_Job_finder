"""Advance TRS adapter — WordPress / WP Job Manager REST API.

Overview
--------
Advance TRS (www.advance-trs.com) is a WordPress site running the WP Job
Manager plugin, which exposes a standard WP REST API for job listings.

Discovery (2026-06-18)
-----------------------
- Endpoint: GET /wp-json/wp/v2/job-listings
- Contract filter: ``?job-types=7`` (taxonomy term 7 = Contract)
- Pagination: ``?per_page=100&page=N`` (N is 1-indexed)
- Total count: ``x-wp-total`` response header
- Total pages: ``x-wp-totalpages`` response header
- robots.txt: allows crawlers; no Cloudflare blocking
- No authentication required

Taxonomy term IDs (job-types)
-------------------------------
  Contract = 7, Permanent = 8

Field map (job-listings[])
---------------------------
  id               → source_listing_id
  title.rendered   → title
  link             → url
  date             → posted_at  (WP local time, treated as UTC)
  content.rendered → description_raw  (full HTML)
  meta._job_location  → location_raw
  meta._job_salary    → salary_raw  (often empty)
  meta._company_name  → employer
  job-types        → contract_type_raw ("Contract" when 7 present)

Pagination strategy
-------------------
  - Fetch page 1; read ``x-wp-totalpages`` from response headers.
  - Fetch remaining pages sequentially with a polite delay.
  - All pages use ``job-types=7`` to limit to contract roles.
  - No keyword search — WP API has none; fetch all and let pipeline filter.
  - Dedup by ``id`` field across all pages.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.advance_trs")

_SOURCE_NAME = "advance_trs"
_EMPLOYER = "Advance Training and Recruitment Services"
_BASE_URL = "https://www.advance-trs.com"
_API_PATH = "/wp-json/wp/v2/job-listings"
_JOB_TYPE_CONTRACT = 7  # WP Job Manager taxonomy term ID for Contract
_DEFAULT_PER_PAGE = 100
_PAGE_DELAY_SECONDS = 3
_REQUEST_TIMEOUT = 30.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
}


# ---------------------------------------------------------------------------
# Pure helpers (module-level so tests can call them directly)
# ---------------------------------------------------------------------------


def _parse_posted_date(raw: str | None) -> datetime | None:
    """Parse WP Job Manager date strings to an aware UTC datetime.

    WP stores dates in site local time without a timezone suffix,
    e.g. ``"2026-06-15T10:00:00"``.  We treat them as UTC (close enough
    for recency filtering).

    Also handles plain ISO dates: ``"2026-06-15"``.
    """
    if not raw:
        return None
    raw = raw.strip()
    # Full ISO datetime without tz
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # Plain date
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    logger.debug("advance_trs: could not parse date string %r", raw)
    return None


def _item_to_raw_listing(item: dict) -> RawListing | None:
    """Map one WP Job Manager job object to a RawListing.

    Returns None for entries missing a required field (id, title, link).
    """
    wp_id = item.get("id")
    if not wp_id:
        return None

    title_block = item.get("title", {})
    title = (title_block.get("rendered") or "").strip()
    if not title:
        return None

    url = (item.get("link") or "").strip()
    if not url:
        return None

    meta: dict = item.get("meta", {})
    location_raw = (meta.get("_job_location") or "").strip() or None
    salary_raw_val = (meta.get("_job_salary") or "").strip() or None
    employer = (meta.get("_company_name") or "").strip() or _EMPLOYER

    posted_at = _parse_posted_date(item.get("date"))

    content_block = item.get("content", {})
    description_raw = (content_block.get("rendered") or "").strip() or None

    job_types: list = item.get("job-types", [])
    contract_type_raw = "Contract" if _JOB_TYPE_CONTRACT in job_types else None

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=str(wp_id),
        url=url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=salary_raw_val,
        contract_type_raw=contract_type_raw,
        metadata={
            "wp_id": wp_id,
            "job_types": job_types,
            "job_expires": meta.get("_job_expires"),
            "application": meta.get("_application"),
        },
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class AdvanceTrsAdapter(SourceAdapter):
    """Adapter for Advance TRS via the WP Job Manager REST API.

    Fetches all contract job listings (job-types=7) in one or two pages
    of up to 100 results each.  No keyword search is used — the API does
    not support keyword filtering, so all contract roles are fetched and
    downstream pipeline filters apply.

    robots.txt: permissive; no bot protection observed.
    Polite delay of ``_PAGE_DELAY_SECONDS`` is applied between pages.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        crawl_delay: int = 3,
        per_page: int = _DEFAULT_PER_PAGE,
        **kwargs: object,
    ) -> None:
        self.crawl_delay = crawl_delay
        self.per_page = per_page

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch all contract listings from the Advance TRS WP API.

        - Paginates using ``x-wp-totalpages`` response header.
        - Deduplicates by WP ``id`` field.
        - Applies ``since`` filter on ``posted_at`` (best-effort; entries
          without a date are always included).
        - Returns [] and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}
        total_pages = 1

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for page in range(1, 999):  # upper bound; breaks on last page
                    if page > 1:
                        await asyncio.sleep(_PAGE_DELAY_SECONDS)

                    items, total_pages = await self._fetch_page(client, page)

                    for item in items:
                        listing = _item_to_raw_listing(item)
                        if listing is None:
                            continue
                        if since and listing.posted_at and listing.posted_at < since:
                            continue
                        lid = listing.source_listing_id
                        if lid and lid not in seen:
                            seen[lid] = listing

                    logger.debug(
                        "advance_trs: page %d/%d → %d items, %d unique so far.",
                        page,
                        total_pages,
                        len(items),
                        len(seen),
                    )

                    if page >= total_pages:
                        break

        except Exception:
            logger.exception(
                "advance_trs: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "advance_trs: fetched %d unique listing(s) "
            "(pages=%d, since=%s).",
            len(listings),
            total_pages,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        page: int,
        *,
        _max_retries: int = 3,
    ) -> tuple[list[dict], int]:
        """Fetch a single page from the WP Job Manager API.

        Returns (items, total_pages).
        Retries up to ``_max_retries`` times on transient failures (network
        errors, non-200 responses, JSON parse errors) with exponential backoff.
        On exhaustion: logs warning, returns ([], 1).
        """
        url = (
            f"{_BASE_URL}{_API_PATH}"
            f"?job-types={_JOB_TYPE_CONTRACT}"
            f"&per_page={self.per_page}"
            f"&page={page}"
        )
        for attempt in range(1, _max_retries + 1):
            try:
                response = await client.get(url)
            except httpx.RequestError as exc:
                logger.warning(
                    "advance_trs: request error for page %d (attempt %d/%d): %s",
                    page, attempt, _max_retries, exc,
                )
                if attempt < _max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return [], 1

            if response.status_code != 200:
                logger.warning(
                    "advance_trs: HTTP %d for page %d (attempt %d/%d)",
                    response.status_code, page, attempt, _max_retries,
                )
                if attempt < _max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return [], 1

            try:
                total_pages = int(response.headers.get("x-wp-totalpages", "1"))
            except (ValueError, TypeError):
                total_pages = 1

            try:
                items = response.json()
            except Exception:
                logger.warning(
                    "advance_trs: JSON parse failed for page %d (attempt %d/%d)",
                    page, attempt, _max_retries,
                )
                if attempt < _max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return [], total_pages

            if not isinstance(items, list):
                logger.warning(
                    "advance_trs: unexpected response body type on page %d: %r",
                    page, type(items),
                )
                return [], total_pages

            return items, total_pages

        return [], 1  # unreachable but satisfies type checker
