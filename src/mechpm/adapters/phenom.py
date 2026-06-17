"""Phenom ATS platform adapter — BAM Careers + Mace Group.

Overview
--------
Both BAM Careers (www.bamcareers.com) and Mace Group Careers
(careers.macegroup.com) run on the Phenom People enterprise ATS platform.
This single adapter handles both sites via config — only the domain, site_path,
and employer_name differ.

Discovery (live recon 2026-06-17)
----------------------------------
- Job data is embedded in-page as JSON in the ``phApp.ddo`` JavaScript variable:
  ``phApp.ddo.eagerLoadRefineSearch.data.jobs``  — 10 jobs per page.
- Total result count is at ``phApp.ddo.eagerLoadRefineSearch.totalHits``.
- Pagination: ``?from=N&s=1`` where N = 0, 10, 20, ... (10 results per page).
  ``?from=0`` is the default (no param needed); ``?from=10&s=1`` is page 2 etc.
- Keyword search: ``?keywords=<encoded>`` added to the search-results URL.
- No authentication required; no Cloudflare or Akamai bot protection.
- robots.txt: permissive — only /chatbot, /iauth, /socialAuth are blocked.

Job URL pattern (from phApp.urlMap)
------------------------------------
  ``{base_url}/{site_path}/job/{jobSeqNo}``

  The full URL map template is ``"job/:jobSeqNo/:title"`` but the title slug is
  optional — the site serves the page without it (confirmed live).

Field map (phApp.ddo.eagerLoadRefineSearch.data.jobs[])
---------------------------------------------------------
  reqId            → source_listing_id  (must be numeric; skip others)
  jobSeqNo         → used to build URL
  title            → title
  companyName      → employer (BAM has it; Mace doesn't — fall back to config)
  city + country   → location_raw
  postedDate       → posted_at (ISO-8601 UTC string)
  descriptionTeaser→ description_raw (snippet)
  salaryRange      → salary_raw (often absent)
  type             → contract_type_raw (Permanent / Full-time / Contract / etc.)

Pagination strategy
-------------------
  For each keyword in ``keywords_list`` the adapter fetches pages until either:
  - an empty-jobs page is returned (natural end of results), OR
  - ``max_pages_per_query`` is reached (safety cap).
  All results are union-deduped by ``source_listing_id`` before return.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.phenom")

_PAGE_SIZE = 10
_PAGE_DELAY_SECONDS = 5  # polite crawl delay between page requests
_REQUEST_TIMEOUT = 30.0
_DEFAULT_MAX_PAGES = 5  # 5 pages × 10 = 50 results per keyword query

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

# Phenom embeds phApp.ddo JSON in a script tag ending with this HTML-escaped comment.
_DDO_MARKER_START = "phApp.ddo = "
_DDO_MARKER_END = "/*--&gt;*/"


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _extract_ddo_json(html: str) -> dict:
    """Extract the phApp.ddo JSON object from the page HTML.

    The JSON is embedded as:
      ``phApp.ddo = {...}; /*--&gt;*/</script>``

    We extract from ``phApp.ddo = `` up to ``/*--&gt;*/``, then isolate the
    first ``{...}`` balanced JSON object (the remainder of the string contains
    subsequent ``phApp.session = {...}`` etc. assignments after a semicolon).
    """
    idx = html.find(_DDO_MARKER_START)
    if idx < 0:
        return {}
    chunk_start = idx + len(_DDO_MARKER_START)
    chunk_end = html.find(_DDO_MARKER_END, chunk_start)
    if chunk_end < 0:
        chunk_end = html.find("</script>", chunk_start)
    if chunk_end < 0:
        return {}

    raw = html[chunk_start:chunk_end].strip()

    # Isolate the first balanced JSON object (stops before any trailing
    # ";  phApp.session = ..." assignments).
    depth = 0
    end = 0
    for i, ch in enumerate(raw):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if not end:
        return {}

    try:
        return json.loads(raw[:end])
    except json.JSONDecodeError:
        logger.debug("phenom: phApp.ddo JSON parse failed for a page.")
        return {}


def _parse_posted_date(raw: str | None) -> datetime | None:
    """Parse Phenom's ISO-8601 UTC date strings.

    Confirmed format: ``2026-06-05T00:00:00.000+0000``
    Also handles: ``2026-06-05T00:00:00.000Z``, plain ``2026-06-05``.
    """
    if not raw:
        return None
    raw = raw.strip()
    # Try ISO 8601 with milliseconds and numeric offset (e.g. +0000 / -0500)
    iso_ms_match = re.match(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([+-]\d{4}|Z)?$", raw
    )
    if iso_ms_match:
        base = iso_ms_match.group(1)
        tz_part = iso_ms_match.group(3) or "Z"
        if tz_part == "+0000" or tz_part == "Z":
            try:
                return datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass
        # Fallback: strip tz and use UTC
        try:
            return datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    # Plain date
    plain_match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if plain_match:
        try:
            return datetime.strptime(plain_match.group(1), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    logger.debug("phenom: could not parse date string %r", raw)
    return None


def _build_location_raw(job: dict) -> str | None:
    """Build a human-readable location string from job data.

    Tries multi_location list first (most complete), then city+country fields.
    """
    multi = job.get("multi_location")
    if multi and isinstance(multi, list) and multi:
        return ", ".join(loc for loc in multi if loc)
    city = job.get("city") or ""
    country = job.get("country") or ""
    parts = [p for p in (city, country) if p]
    return ", ".join(parts) if parts else None


def _parse_page(
    html: str,
    page_url: str,
    base_url: str,
    site_path: str,
    source_name: str,
    fallback_employer: str,
) -> tuple[list[dict], int | None]:
    """Parse one Phenom search-results HTML page.

    Returns:
        (jobs_list, total_hits) — ``total_hits`` is None when not found.
    """
    ddo = _extract_ddo_json(html)
    if not ddo:
        logger.warning(
            "phenom[%s]: could not extract phApp.ddo JSON from %s",
            source_name,
            page_url,
        )
        return [], None

    eager = ddo.get("eagerLoadRefineSearch", {})
    total_hits: int | None = eager.get("totalHits")
    data = eager.get("data", {})
    raw_jobs: list[dict] = data.get("jobs", [])

    return raw_jobs, total_hits


def _job_to_raw_listing(
    job: dict,
    base_url: str,
    site_path: str,
    source_name: str,
    fallback_employer: str,
) -> RawListing | None:
    """Map one Phenom job dict to a RawListing.

    Returns None for invalid entries (missing title, non-numeric reqId).
    """
    req_id = job.get("reqId", "")
    # Skip non-numeric placeholder entries (e.g. "Required Id" in Mace HTML)
    if not req_id or not str(req_id).isdigit():
        return None

    title = (job.get("title") or "").strip()
    if not title:
        return None

    job_seq_no = job.get("jobSeqNo") or str(req_id)
    listing_url = f"{base_url}{site_path}/job/{job_seq_no}"

    employer = (job.get("companyName") or "").strip() or fallback_employer
    location_raw = _build_location_raw(job)

    salary_raw_val = job.get("salaryRange") or None
    if salary_raw_val and not str(salary_raw_val).strip():
        salary_raw_val = None

    posted_at = _parse_posted_date(job.get("postedDate"))

    description_raw = (job.get("descriptionTeaser") or "").strip() or None

    contract_type_raw = (job.get("type") or "").strip() or None

    return RawListing(
        source=source_name,
        source_listing_id=str(req_id),
        url=listing_url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=salary_raw_val,
        contract_type_raw=contract_type_raw,
        metadata={
            "jobSeqNo": job_seq_no,
            "job_type_raw": job.get("type"),
            "detail_fetched": False,
        },
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

class PhenomAdapter(SourceAdapter):
    """Adapter for Phenom People ATS job sites (BAM Careers, Mace Group).

    A single class handles multiple Phenom-powered sites via config.
    The ``domain`` and ``site_path`` config keys determine which site is
    scraped; ``employer_name`` provides the fallback employer for sites that
    do not embed a companyName in search-result cards.

    Pagination uses Phenom's ``?from=N&s=1`` query parameters (10 results per
    page).  For each keyword in ``keywords_list`` the adapter fetches pages up
    to ``max_pages_per_query``, then union-deduplicates across all keywords by
    ``source_listing_id``.

    robots.txt: both BAM and Mace sites are permissive (only /chatbot, /iauth,
    /socialAuth disallowed).  Crawl-delay is honoured via ``_PAGE_DELAY_SECONDS``
    between every page request.
    """

    def __init__(
        self,
        name: str,
        domain: str,
        site_path: str,
        employer_name: str,
        keywords_list: list[str] | None = None,
        crawl_delay: int = 5,
        max_pages_per_query: int = _DEFAULT_MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.name = name
        self.domain = domain
        self.site_path = site_path.rstrip("/")
        self.employer_name = employer_name
        self.keywords_list: list[str] = keywords_list or [
            "project manager",
            "project engineer",
        ]
        self.crawl_delay = crawl_delay
        self.max_pages_per_query = max_pages_per_query

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}"

    @property
    def search_url(self) -> str:
        return f"{self.base_url}{self.site_path}/search-results"

    def _build_page_url(self, keyword: str, from_offset: int) -> str:
        """Build a Phenom search-results URL for a given keyword and offset."""
        encoded_kw = quote_plus(keyword)
        if from_offset == 0:
            return f"{self.search_url}?keywords={encoded_kw}"
        return f"{self.search_url}?keywords={encoded_kw}&from={from_offset}&s=1"

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch Phenom job listings by iterating keywords and paginating.

        - Iterates each entry in ``keywords_list``.
        - Paginates up to ``max_pages_per_query`` pages per keyword.
        - Deduplicates by ``source_listing_id`` across all keyword/page fetches.
        - Applies ``since`` filter on ``posted_at`` (best-effort; entries
          without a date are always included).
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
                    for page_num in range(1, self.max_pages_per_query + 1):
                        from_offset = (page_num - 1) * _PAGE_SIZE
                        url = self._build_page_url(keyword, from_offset)

                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False

                        raw_jobs, total_hits = await self._fetch_page(client, url)

                        if not raw_jobs:
                            logger.debug(
                                "phenom[%s]: empty page at offset %d for "
                                "keyword %r — stopping pagination.",
                                self.name,
                                from_offset,
                                keyword,
                            )
                            break

                        new_count = 0
                        for job in raw_jobs:
                            listing = _job_to_raw_listing(
                                job,
                                self.base_url,
                                self.site_path,
                                self.name,
                                self.employer_name,
                            )
                            if listing is None:
                                continue
                            if since and listing.posted_at and listing.posted_at < since:
                                continue
                            lid = listing.source_listing_id
                            if lid and lid not in seen:
                                seen[lid] = listing
                                new_count += 1

                        logger.debug(
                            "phenom[%s]: keyword=%r page=%d offset=%d "
                            "→ %d raw, %d new unique (total_hits=%s).",
                            self.name,
                            keyword,
                            page_num,
                            from_offset,
                            len(raw_jobs),
                            new_count,
                            total_hits,
                        )

                        if len(raw_jobs) < _PAGE_SIZE:
                            logger.debug(
                                "phenom[%s]: partial page (%d < %d) — "
                                "last page for keyword %r.",
                                self.name,
                                len(raw_jobs),
                                _PAGE_SIZE,
                                keyword,
                            )
                            break

        except Exception:
            logger.exception(
                "phenom[%s]: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                self.name,
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "phenom[%s]: fetched %d unique listing(s) "
            "(keywords=%d, max_pages=%d, since=%s).",
            self.name,
            len(listings),
            len(self.keywords_list),
            self.max_pages_per_query,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> tuple[list[dict], int | None]:
        """Fetch and parse a single Phenom search-results page.

        Returns (raw_jobs_list, total_hits_or_None).
        On HTTP / parse failure: logs warning, returns ([], None).
        """
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning("phenom[%s]: request error for %s: %s", self.name, url, exc)
            return [], None

        if response.status_code != 200:
            logger.warning(
                "phenom[%s]: HTTP %d for %s",
                self.name,
                response.status_code,
                url,
            )
            return [], None

        html = response.text
        raw_jobs, total_hits = _parse_page(
            html,
            page_url=url,
            base_url=self.base_url,
            site_path=self.site_path,
            source_name=self.name,
            fallback_employer=self.employer_name,
        )
        return raw_jobs, total_hits


# ---------------------------------------------------------------------------
# Module-level parse helper (used by tests without making HTTP requests)
# ---------------------------------------------------------------------------

def parse_html(
    html: str,
    page_url: str,
    base_url: str,
    site_path: str,
    source_name: str,
    fallback_employer: str,
) -> list[RawListing]:
    """Parse a Phenom search-results HTML page and return RawListings.

    Extracted as a standalone function so fixture-based unit tests can call
    it directly without any network I/O.
    """
    raw_jobs, _ = _parse_page(
        html,
        page_url=page_url,
        base_url=base_url,
        site_path=site_path,
        source_name=source_name,
        fallback_employer=fallback_employer,
    )
    listings = []
    for job in raw_jobs:
        listing = _job_to_raw_listing(
            job,
            base_url=base_url,
            site_path=site_path,
            source_name=source_name,
            fallback_employer=fallback_employer,
        )
        if listing is not None:
            listings.append(listing)
    return listings
