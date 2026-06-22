"""Arcadis careers adapter — Eightfold AI public JSON API.

Overview
--------
Arcadis (global engineering consultancy) powers their careers site at
jobs.arcadis.com using the Eightfold AI platform.  The search API is
publicly accessible — no authentication required, no CAPTCHA on the API
(only on the browser UI), and robots.txt explicitly allows /api/pcsx.

Discovery (live recon 2026-06-22)
-----------------------------------
- GET https://jobs.arcadis.com/api/pcsx/search
    ?domain=arcadis.com
    &query={keyword}
    &offset={offset}        # 0-based; default 0
    &limit={limit}          # results per page; default 10
    &location=United+Kingdom  # reduces ~770 global → ~150 UK results per query
  Response:
    {
      "status": 200,
      "data": {
        "positions": [{...}, ...],
        "count": 150,
        "filterDef": {...},
        ...
      }
    }

Position object fields
-----------------------
  id                   → source_listing_id  (int → str)
  displayJobId         → metadata.display_job_id
  name                 → title
  locations            → location_raw  (list[str] → joined)
  standardizedLocations→ metadata.standardized_locations
  postedTs             → posted_at  (Unix timestamp seconds → UTC datetime)
  creationTs           → metadata.creation_ts
  department           → metadata.department
  workLocationOption   → contract_type_raw  (hybrid / onsite / remote)
  atsJobId             → metadata.ats_job_id
  positionUrl          → URL suffix → full URL via api_base

No salary or description fields are present in the search API response.
Description would require a per-listing detail fetch (not implemented here
to keep crawl pressure low).

Pagination
----------
  offset=0, 10, 20, ...  up to ``count``.  Capped per keyword by
  ``max_pages_per_query`` (default 5 pages × 10 = 50 results).

Location filter
---------------
  ``&location=United+Kingdom`` is sent when ``location`` param is set
  (default "United Kingdom").  Pass ``location=""`` to disable.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.arcadis")

_DEFAULT_API_BASE = "https://jobs.arcadis.com"
_DEFAULT_DOMAIN = "arcadis.com"
_DEFAULT_LOCATION = "United Kingdom"
_PAGE_SIZE = 10
_PAGE_DELAY_SECONDS = 2
_REQUEST_TIMEOUT = 30.0
_DEFAULT_MAX_PAGES = 5  # 5 × 10 = 50 results per keyword
_SOURCE_NAME = "arcadis"
_EMPLOYER = "Arcadis"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://jobs.arcadis.com/careers",
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_posted_ts(ts: int | float | None) -> datetime | None:
    """Convert a Unix timestamp (seconds) to an aware UTC datetime."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        logger.debug("arcadis: could not parse timestamp %r", ts)
        return None


def _build_location_raw(position: dict) -> str | None:
    """Join the ``locations`` list into a single readable string."""
    locs = position.get("locations")
    if locs and isinstance(locs, list):
        parts = [str(loc).strip() for loc in locs if loc]
        return ", ".join(parts) if parts else None
    return None


def _position_to_raw_listing(
    position: dict,
    api_base: str,
) -> RawListing | None:
    """Map one Eightfold position dict to a RawListing.

    Returns None for entries with missing id or title.
    """
    pos_id = position.get("id")
    if pos_id is None:
        return None

    title = (position.get("name") or "").strip()
    if not title:
        return None

    source_listing_id = str(pos_id)

    # URL: positionUrl is relative (e.g. "/careers/job/563671530957634")
    position_url_suffix = position.get("positionUrl") or f"/careers/job/{pos_id}"
    url = f"{api_base.rstrip('/')}{position_url_suffix}"

    location_raw = _build_location_raw(position)
    posted_at = _parse_posted_ts(position.get("postedTs"))

    # workLocationOption carries hybrid / onsite / remote — useful as a proxy
    # for contract_type until description detail is available.
    work_opt = (position.get("workLocationOption") or "").strip() or None

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=source_listing_id,
        url=url,
        title=title,
        employer=_EMPLOYER,
        location_raw=location_raw,
        description_raw=None,
        posted_at=posted_at,
        salary_raw=None,
        contract_type_raw=work_opt,
        metadata={
            "display_job_id": position.get("displayJobId"),
            "department": position.get("department"),
            "ats_job_id": position.get("atsJobId"),
            "standardized_locations": position.get("standardizedLocations"),
            "creation_ts": position.get("creationTs"),
            "is_hot": position.get("isHot"),
        },
    )


def parse_response(
    data: dict,
    api_base: str,
) -> tuple[list[RawListing], int]:
    """Parse one Eightfold API response dict.

    Returns:
        (listings, total_count) — listings mapped from positions[]; total_count
        from data["count"] (0 if absent or non-int).
    """
    positions: list[dict] = data.get("positions") or []
    try:
        total_count = int(data.get("count", 0))
    except (TypeError, ValueError):
        total_count = 0

    listings: list[RawListing] = []
    for pos in positions:
        listing = _position_to_raw_listing(pos, api_base)
        if listing is not None:
            listings.append(listing)
    return listings, total_count


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

class ArcadisAdapter(SourceAdapter):
    """Adapter for Arcadis Engineering Consultancy careers (Eightfold AI API).

    Uses the public ``/api/pcsx/search`` JSON endpoint — no auth required.
    robots.txt explicitly allows ``/api/pcsx``.

    For each keyword the adapter paginates (offset-based, 10 results/page)
    up to ``max_pages_per_query`` pages.  All results are union-deduplicated
    by ``source_listing_id`` across keywords before return.

    A ``&location=United+Kingdom`` filter is appended by default to reduce
    global results to UK-only.  Set ``location=""`` in config to disable.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        api_base: str = _DEFAULT_API_BASE,
        domain: str = _DEFAULT_DOMAIN,
        keywords_list: list[str] | None = None,
        crawl_delay: int = 2,
        max_pages_per_query: int = _DEFAULT_MAX_PAGES,
        location: str = _DEFAULT_LOCATION,
        **kwargs: object,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.domain = domain
        self.keywords_list: list[str] = keywords_list or [
            "project manager",
            "project director",
        ]
        self.crawl_delay = crawl_delay
        self.max_pages_per_query = max_pages_per_query
        self.location = location

    def _build_url(self, keyword: str, offset: int) -> str:
        """Construct the search API URL for a given keyword and offset."""
        encoded_kw = quote_plus(keyword)
        params = (
            f"domain={self.domain}"
            f"&query={encoded_kw}"
            f"&offset={offset}"
            f"&limit={_PAGE_SIZE}"
        )
        if self.location:
            params += f"&location={quote_plus(self.location)}"
        return f"{self.api_base}/api/pcsx/search?{params}"

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch Arcadis listings by iterating keywords and paginating.

        - Iterates each entry in ``keywords_list``.
        - Paginates up to ``max_pages_per_query`` per keyword using offset.
        - Deduplicates by ``source_listing_id`` across all fetches.
        - Applies ``since`` filter on ``posted_at`` (entries without a date
          are always included).
        - Returns [] and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}
        first_request = True

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for keyword in self.keywords_list:
                    for page_num in range(self.max_pages_per_query):
                        offset = page_num * _PAGE_SIZE
                        url = self._build_url(keyword, offset)

                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False

                        listings, total_count = await self._fetch_page(client, url)

                        if listings is None:
                            # Hard failure — stop this keyword
                            break

                        new_count = 0
                        for listing in listings:
                            if since and listing.posted_at and listing.posted_at < since:
                                continue
                            lid = listing.source_listing_id
                            if lid not in seen:
                                seen[lid] = listing
                                new_count += 1

                        logger.debug(
                            "arcadis: keyword=%r page=%d offset=%d "
                            "→ %d parsed, %d new unique (total_count=%s).",
                            keyword,
                            page_num + 1,
                            offset,
                            len(listings),
                            new_count,
                            total_count,
                        )

                        # Stop paginating if we've reached all available results
                        if not listings or (offset + _PAGE_SIZE) >= total_count:
                            break

        except Exception:
            logger.exception(
                "arcadis: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        result = list(seen.values())
        logger.info(
            "arcadis: fetched %d unique listing(s) "
            "(keywords=%d, max_pages=%d, since=%s).",
            len(result),
            len(self.keywords_list),
            self.max_pages_per_query,
            since,
        )
        return result

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> tuple[list[RawListing] | None, int]:
        """Fetch and parse one API page.

        Returns:
            (listings, total_count) on success.
            (None, 0) on HTTP or parse failure (logs warning).
        """
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning("arcadis: request error for %s: %s", url, exc)
            return None, 0

        if response.status_code != 200:
            logger.warning(
                "arcadis: HTTP %d for %s",
                response.status_code,
                url,
            )
            return None, 0

        try:
            body = response.json()
        except Exception as exc:
            logger.warning("arcadis: JSON parse error for %s: %s", url, exc)
            return None, 0

        api_status = body.get("status")
        if api_status != 200:
            logger.warning(
                "arcadis: API status %r for %s — skipping.",
                api_status,
                url,
            )
            return None, 0

        data = body.get("data") or {}
        listings, total_count = parse_response(data, self.api_base)
        return listings, total_count
