"""Reed.co.uk official JSON API adapter.

Authentication: HTTP Basic Auth — API key as username, empty password.
Endpoint:       GET https://www.reed.co.uk/api/1.0/search
Rate limit:     10 req/min (free tier) → 6 s sleep between paginated calls.
Pagination:     resultsToSkip increments by resultsToTake until empty page
                or safety_cap reached.
Since filter:   Reed API does not expose a postedAfter query param;
                client-side filter on posted_at is applied instead.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.reed")

REED_API_BASE = "https://www.reed.co.uk/api/1.0"
_DEFAULT_KEYWORDS = "project manager mechanical engineering"
_DEFAULT_LOCATION = ""
_DEFAULT_RESULTS_TO_TAKE = 100
_DEFAULT_SAFETY_CAP = 500
_PAGE_DELAY_SECONDS = 6  # 10 req/min ceiling → 6 s between successive requests
_REQUEST_TIMEOUT = 30.0


def _parse_reed_date(date_str: str | None) -> datetime | None:
    """Parse Reed's date string (various formats) to a UTC-aware datetime."""
    if not date_str:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("Could not parse Reed date string: %r", date_str)
    return None


def _build_salary_raw(job: dict[str, Any]) -> str | None:
    """Construct a human-readable salary string from Reed API fields."""
    min_sal = job.get("minimumSalary")
    max_sal = job.get("maximumSalary")
    currency = job.get("currency") or "GBP"
    salary_type = job.get("salaryType") or ""

    if min_sal is None and max_sal is None:
        return None

    if min_sal is not None and max_sal is not None and min_sal != max_sal:
        base = f"{currency}{min_sal:,.0f}–{currency}{max_sal:,.0f}"
    elif min_sal is not None:
        base = f"{currency}{min_sal:,.0f}"
    else:
        base = f"{currency}{max_sal:,.0f}"

    return f"{base} {salary_type}".strip() if salary_type else base


class ReedAdapter(SourceAdapter):
    """Adapter for the Reed.co.uk official JSON API."""

    name = "reed"

    def __init__(
        self,
        api_key: str,
        crawl_delay: int = 0,
        keywords: str = _DEFAULT_KEYWORDS,
        location: str = _DEFAULT_LOCATION,
        results_to_take: int = _DEFAULT_RESULTS_TO_TAKE,
        safety_cap: int = _DEFAULT_SAFETY_CAP,
    ) -> None:
        self.api_key = api_key
        self.crawl_delay = crawl_delay
        self.keywords = keywords
        self.location = location
        self.results_to_take = results_to_take
        self.safety_cap = safety_cap

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch contract PM listings from Reed, paginating until empty or safety cap.

        Sleeps _PAGE_DELAY_SECONDS between requests to stay within 10 req/min.
        Filters client-side on posted_at when ``since`` is provided.
        Returns [] on any unrecoverable error.
        """
        if not self.api_key:
            logger.warning("REED_API_KEY is not set — skipping Reed fetch.")
            return []

        listings: list[RawListing] = []
        skip = 0

        try:
            async with httpx.AsyncClient(
                auth=(self.api_key, ""),
                timeout=_REQUEST_TIMEOUT,
            ) as client:
                while len(listings) < self.safety_cap:
                    page = await self._fetch_page(client, skip)

                    if not page:
                        break

                    for job in page:
                        raw = self._map_to_raw_listing(job)
                        if since and raw.posted_at and raw.posted_at < since:
                            continue
                        listings.append(raw)

                    if len(page) < self.results_to_take:
                        # Last page — no further pages to fetch.
                        break

                    skip += self.results_to_take

                    if len(listings) < self.safety_cap:
                        await asyncio.sleep(_PAGE_DELAY_SECONDS)

        except Exception:
            logger.exception(
                "Reed fetch failed — returning partial results (%d so far).",
                len(listings),
            )
            return listings

        logger.info(
            "Reed: fetched %d listing(s) (pages_fetched=%d, since=%s).",
            len(listings),
            (skip // self.results_to_take) + 1,
            since,
        )
        return listings

    async def _fetch_page(
        self, client: httpx.AsyncClient, skip: int
    ) -> list[dict[str, Any]]:
        """Fetch one page of Reed search results; returns [] on HTTP/network error."""
        params: dict[str, Any] = {
            "keywords": self.keywords,
            "contract": "true",
            "resultsToTake": self.results_to_take,
            "resultsToSkip": skip,
        }
        if self.location:
            params["locationName"] = self.location
        try:
            response = await client.get(f"{REED_API_BASE}/search", params=params)
            response.raise_for_status()
            return response.json().get("results", [])
        except httpx.HTTPStatusError as exc:
            logger.warning("Reed API HTTP error (skip=%d): %s", skip, exc)
            return []
        except Exception:
            logger.exception("Reed API request failed (skip=%d).", skip)
            return []

    def _map_to_raw_listing(self, job: dict[str, Any]) -> RawListing:
        """Map a Reed API job dict to a canonical RawListing."""
        return RawListing(
            source="reed",
            source_listing_id=str(job.get("jobId", "")),
            url=job.get("jobUrl", ""),
            title=job.get("jobTitle", ""),
            employer=job.get("employerName"),
            agency=None,
            location_raw=job.get("locationName"),
            description_raw=job.get("jobDescription"),
            posted_at=_parse_reed_date(job.get("date")),
            salary_raw=_build_salary_raw(job),
            contract_type_raw="contract",
            metadata={
                "currency": job.get("currency"),
                "salary_type": job.get("salaryType"),
                "applications": job.get("applications"),
                "expiration_date": job.get("expirationDate"),
            },
        )


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    _api_key = os.environ.get("REED_API_KEY", "")
    if not _api_key:
        print("REED_API_KEY not set in .env — cannot run self-test.")
    else:
        _adapter = ReedAdapter(api_key=_api_key)
        _results = asyncio.run(_adapter.fetch())
        print(f"Fetched {len(_results)} listing(s) from Reed.")
        if _results:
            first = _results[0]
            print(f"  Sample: '{first.title}' | {first.location_raw} | {first.salary_raw}")
