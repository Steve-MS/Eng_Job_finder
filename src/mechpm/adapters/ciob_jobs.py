"""CIOB Jobs adapter — WordPress REST API.

Overview
--------
CIOB Jobs (ciobjobs.com) is the official job board of the Chartered Institute
of Building.  The site exposes a WordPress REST API for job listings.

Discovery (2026-06-19)
-----------------------
- Endpoint: GET /wp-json/wp/v2/job?search={keyword}&per_page=100&page=N
- Pagination: ``x-wp-total`` and ``x-wp-totalpages`` response headers
- Total jobs: ~108,983 across all categories (keyword search limits results)
- No authentication required
- robots.txt: permissive; no bot-management blocking observed

Field map (job object → RawListing)
--------------------------------------
  id                    → source_listing_id  (str)
  title.rendered        → title
  link                  → url
  date                  → posted_at  (WP local time, treated as UTC)
  content.rendered      → description_raw  (full HTML)
  Location from HTML    → location_raw  (best-effort regex)
  Salary from HTML      → salary_raw    (best-effort regex)
  Employer from HTML    → employer      (best-effort regex)
  industry-sector       → metadata.industry_sector
  job-sector            → metadata.job_sector
  job-specialism        → metadata.job_specialism

Fetch strategy
--------------
  For each keyword in keywords_list, fetch pages 1..max_pages using
  GET /wp-json/wp/v2/job?search={keyword}&per_page=per_page&page=N.
  Pagination is bounded by x-wp-totalpages (capped at max_pages).
  Deduplication by id across all keywords and pages.
  A 3s polite delay is applied between pages.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.ciob_jobs")

_SOURCE_NAME = "ciob_jobs"
_DEFAULT_API_BASE = "https://ciobjobs.com"
_API_PATH = "/wp-json/wp/v2/job"
_DEFAULT_PER_PAGE = 100
_DEFAULT_MAX_PAGES = 5
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

# Best-effort extraction regexes applied to HTML-stripped description text
_RE_STRIP_HTML = re.compile(r"<[^>]+>")
_RE_LOCATION = re.compile(
    r"(?:location|place)\s*[:\-]\s*([A-Za-z][^\n<]{2,80})",
    re.IGNORECASE,
)
_RE_SALARY = re.compile(
    r"(?:salary|package|rate|pay)\s*[:\-]\s*([£$€\d][^\n<]{2,80})",
    re.IGNORECASE,
)
_RE_EMPLOYER = re.compile(
    r"(?:employer|company|organisation|organization|client)\s*[:\-]\s*([A-Za-z][^\n<]{2,100})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure helpers (module-level so tests can call them directly)
# ---------------------------------------------------------------------------


def _strip_html(html: str) -> str:
    """Remove HTML tags, returning plain text with excess whitespace collapsed."""
    return _RE_STRIP_HTML.sub(" ", html).strip()


def _extract_field(pattern: re.Pattern[str], text: str) -> str | None:
    """Return the first capture group of ``pattern`` in ``text``, stripped.

    Returns None when there is no match or the capture is empty/whitespace.
    """
    m = pattern.search(text)
    if not m:
        return None
    value = m.group(1).strip()
    return value if value else None


def _parse_wp_date(raw: str | None) -> datetime | None:
    """Parse a WordPress date string to a UTC-aware datetime.

    WP stores dates in site local time without a timezone suffix,
    e.g. ``"2026-06-19T09:35:51"``.  We treat them as UTC.

    Also handles plain ISO dates: ``"2026-06-19"``.
    Returns None on any parse failure.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    logger.debug("ciob_jobs: could not parse date string %r", raw)
    return None


def _item_to_raw_listing(item: dict) -> RawListing | None:
    """Map one WP REST API job object to a RawListing.

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

    posted_at = _parse_wp_date(item.get("date"))

    content_block = item.get("content", {})
    description_raw = (content_block.get("rendered") or "").strip() or None

    plain_text = _strip_html(description_raw) if description_raw else ""
    location_raw = _extract_field(_RE_LOCATION, plain_text)
    salary_raw = _extract_field(_RE_SALARY, plain_text)
    employer = _extract_field(_RE_EMPLOYER, plain_text)

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
        salary_raw=salary_raw,
        contract_type_raw=None,
        metadata={
            "wp_id": wp_id,
            "industry_sector": item.get("industry-sector", []),
            "job_sector": item.get("job-sector", []),
            "job_specialism": item.get("job-specialism", []),
        },
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class CiobJobsAdapter(SourceAdapter):
    """Adapter for CIOB Jobs via the WordPress REST API.

    For each keyword in keywords_list, fetches pages 1..max_pages from
    GET /wp-json/wp/v2/job?search={keyword}&per_page={per_page}&page=N.
    Deduplicates by WP id across all keywords and pages.
    Applies since filter on posted_at.

    robots.txt: permissive; no bot-management blocking observed (2026-06-19).
    Polite delay of ``_PAGE_DELAY_SECONDS`` is applied between pages.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        api_base: str = _DEFAULT_API_BASE,
        crawl_delay: int = 2,
        keywords_list: list[str] | None = None,
        per_page: int = _DEFAULT_PER_PAGE,
        max_pages: int = _DEFAULT_MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.crawl_delay = crawl_delay
        self.keywords_list = keywords_list or []
        self.per_page = per_page
        self.max_pages = max_pages

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch job listings for all keywords from the CIOB Jobs WP REST API.

        - Iterates keywords_list, fetching paginated results for each.
        - Deduplicates by WP id across all keywords and pages.
        - Applies the ``since`` filter on ``posted_at`` (entries without a date
          are always included).
        - Returns [] and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for keyword in self.keywords_list:
                    await self._fetch_keyword(client, keyword, since, seen)
        except Exception:
            logger.exception(
                "ciob_jobs: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "ciob_jobs: fetched %d unique listing(s) across %d keyword(s) (since=%s).",
            len(listings),
            len(self.keywords_list),
            since,
        )
        return listings

    async def _fetch_keyword(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        since: datetime | None,
        seen: dict[str, RawListing],
    ) -> None:
        """Fetch all pages for a single keyword, adding results to seen."""
        total_pages = 1
        for page in range(1, self.max_pages + 1):
            if page > 1:
                await asyncio.sleep(_PAGE_DELAY_SECONDS)

            items, total_pages = await self._fetch_page(client, keyword, page)

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
                "ciob_jobs: keyword=%r page %d/%d → %d items, %d unique so far.",
                keyword,
                page,
                total_pages,
                len(items),
                len(seen),
            )

            if page >= total_pages:
                break

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        page: int,
    ) -> tuple[list[dict], int]:
        """Fetch a single page from the CIOB Jobs WP REST API.

        Returns (items, total_pages).
        On HTTP / parse failure: logs warning, returns ([], 1).
        """
        url = (
            f"{self.api_base}{_API_PATH}"
            f"?search={quote_plus(keyword)}"
            f"&per_page={self.per_page}"
            f"&page={page}"
        )
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning(
                "ciob_jobs: request error for keyword=%r page %d: %s",
                keyword,
                page,
                exc,
            )
            return [], 1

        if response.status_code != 200:
            logger.warning(
                "ciob_jobs: HTTP %d for keyword=%r page %d",
                response.status_code,
                keyword,
                page,
            )
            return [], 1

        try:
            total_pages = int(response.headers.get("x-wp-totalpages", "1"))
        except (ValueError, TypeError):
            total_pages = 1

        try:
            items = response.json()
        except Exception:
            logger.warning(
                "ciob_jobs: JSON parse failed for keyword=%r page %d",
                keyword,
                page,
            )
            return [], total_pages

        if not isinstance(items, list):
            logger.warning(
                "ciob_jobs: unexpected response body type for keyword=%r page %d: %r",
                keyword,
                page,
                type(items),
            )
            return [], total_pages

        return items, total_pages
