"""Aviation Job Search adapter — Sitemap + LD+JSON strategy.

Why this approach (not the original CSS-selector scraping):
    The search results page (aviationjobsearch.com/en-GB/jobs) is a fully
    client-side app.  ``AppData.is_ssr`` is ``false`` in the embedded page
    data; the ``#searchResults`` div is empty in the static HTML response.
    The internal AJAX endpoint (``/api/v1/jobs``) is explicitly
    ``Disallow``-ed in robots.txt and must not be called.

    Individual job-detail pages (``/jobs/{cat}/{sub}/{title-id}``) ARE
    server-side rendered and embed a rich ``schema.org/JobPosting`` JSON-LD
    block.  The public sitemap (explicitly listed in robots.txt) provides
    all active job-detail URLs.

Strategy:
    1. Fetch ``/en-GB/sitemap/jobs.xml`` (robots.txt lists this explicitly).
    2. Filter job URLs by ``/management/`` category path or PM-keyword title slug.
    3. Apply optional ``lastmod``-based pre-filter when *since* is supplied.
    4. Fetch each matched job-detail page (3 s crawl-delay between requests).
    5. Extract the ``schema.org/JobPosting`` LD+JSON block from the SSR page.
    6. Map structured JSON to a ``RawListing``.

robots.txt: ``Disallow: /api/*`` only; ``/jobs/*`` and ``/en-GB/sitemap/*`` allowed.
Crawl-delay: 3 s between every HTTP request (binding team decision).
Aggregator: True — board aggregates employer ATS feeds; dedup on source_listing_id.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.aviation_job_search")

_BASE_URL = "https://www.aviationjobsearch.com"
_SITEMAP_URL = f"{_BASE_URL}/en-GB/sitemap/jobs.xml"
_REQUEST_TIMEOUT = 30.0
_PAGE_DELAY_SECONDS = 3
_MAX_JOBS = 50  # safety cap: 50 × 3s = ~150s max crawl time per run

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # Omit 'br': httpx has no built-in Brotli decoder
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# URL path patterns for relevant job categories / title keywords.
# A URL matches if its path contains /management/ OR the title slug contains
# a PM-adjacent keyword.
_RELEVANT_PATTERNS = [
    re.compile(r"/management/"),
    re.compile(r"/engineering/"),              # NEW: engineering category
    re.compile(r"project[_-]manag", re.I),
    re.compile(r"programme[_-]manag", re.I),
    re.compile(r"project[_-]lead", re.I),
    re.compile(r"project[_-]coord", re.I),
    re.compile(r"engineering[_-]manager", re.I),  # NEW: eng manager roles
    re.compile(r"program[_-]manager", re.I),       # NEW: US spelling in slugs
]

# Captures (loc, optional lastmod) from each <url> block in the sitemap XML.
_SITEMAP_RE = re.compile(
    r"<url>\s*<loc>([^<]+)</loc>"
    r"(?:.*?<lastmod>([^<]+)</lastmod>)?",
    re.DOTALL,
)

# Extracts the trailing numeric job ID from a URL slug.
# URL format: {title-slug}-by-{employer-slug}-{numeric-job-id}
_JOB_ID_RE = re.compile(r"-(\d+)$")


# ---------------------------------------------------------------------------
# Pure-function helpers (importable for unit tests)
# ---------------------------------------------------------------------------

def _is_relevant_url(url: str) -> bool:
    """Return True if the URL path matches any relevant category / keyword pattern."""
    path = urlparse(url).path
    return any(p.search(path) for p in _RELEVANT_PATTERNS)


def _extract_job_id(url: str) -> str:
    """Extract the numeric job ID from the trailing segment of the URL slug.

    Example: …/engineering-project-manager-by-jmc-recruitment-solutions-607645
    → "607645"
    """
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    m = _JOB_ID_RE.search(slug)
    return m.group(1) if m else slug


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse an ISO date string from schema.org/JobPosting to UTC-aware datetime."""
    if not date_str:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    logger.debug("Aviation Job Search: could not parse date string %r", date_str)
    return None


def _parse_ldjson_job(html: str) -> dict[str, Any] | None:
    """Extract the first schema.org/JobPosting LD+JSON dict from SSR HTML.

    Iterates all <script> tags, skipping those whose ``type`` attribute does
    not contain ``ld+json``, and returns the first that JSON-parses to a
    ``{"@type": "JobPosting"}`` dict.
    """
    try:
        tree = HTMLParser(html)
    except Exception:
        return None
    for node in tree.css("script"):
        if "ld+json" not in (node.attributes.get("type") or "").lower():
            continue
        try:
            data = json.loads(node.text())
        except (json.JSONDecodeError, Exception):
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _map_ldjson_job(
    job: dict[str, Any],
    source_url: str,
) -> RawListing | None:
    """Map a schema.org/JobPosting dict to a canonical RawListing.

    Field mapping confirmed 2026-06-15 against live Aviation Job Search pages::

        title                      → title
        url                        → url (absolute, canonical)
        datePosted                 → posted_at  (ISO date, e.g. "2026-05-26")
        validThrough               → metadata["valid_through"]
        hiringOrganization.name    → employer
        jobLocation.address.*      → location_raw (locality, region, country)
        employmentType             → contract_type_raw  (e.g. "FULL_TIME")
        description                → description_raw (HTML string)
        identifier.value           → metadata["org_id"] (employer org ID)
        Trailing -NNN in URL       → source_listing_id
    """
    title = job.get("title")
    if not title:
        return None

    url = job.get("url") or source_url
    listing_id = _extract_job_id(url) or _extract_job_id(source_url)
    if not listing_id:
        return None

    # --- employer ---
    hiring_org = job.get("hiringOrganization") or {}
    employer = hiring_org.get("name") if isinstance(hiring_org, dict) else None

    # --- location: addressLocality, addressRegion, addressCountry ---
    job_location = job.get("jobLocation") or {}
    address: dict[str, Any] = {}
    if isinstance(job_location, dict):
        address = job_location.get("address") or {}
    location_parts: list[str] = []
    if isinstance(address, dict):
        for field in ("addressLocality", "addressRegion", "addressCountry"):
            val = address.get(field)
            if val and str(val) not in location_parts:
                location_parts.append(str(val))
    location_raw = ", ".join(location_parts) if location_parts else None

    # --- dates ---
    posted_at = _parse_date(job.get("datePosted"))
    valid_through = job.get("validThrough")

    # --- employment type ---
    employment_type = job.get("employmentType")  # "FULL_TIME", "CONTRACTOR", etc.

    # --- description (HTML) ---
    description_raw = job.get("description") or None

    # --- salary (schema.org baseSalary — rarely populated on this site) ---
    salary_raw: str | None = None
    base_salary = job.get("baseSalary")
    if isinstance(base_salary, dict):
        val = base_salary.get("value") or {}
        currency = base_salary.get("currency") or "GBP"
        if isinstance(val, dict):
            min_v = val.get("minValue")
            max_v = val.get("maxValue")
            if min_v is not None and max_v is not None:
                salary_raw = f"{currency} {min_v}–{max_v}"
            elif min_v is not None:
                salary_raw = f"{currency} {min_v}"
            elif max_v is not None:
                salary_raw = f"{currency} {max_v}"

    # --- metadata ---
    identifier = job.get("identifier") or {}
    org_id = identifier.get("value") if isinstance(identifier, dict) else None
    metadata: dict[str, Any] = {
        "aggregator": True,
        "employment_type": employment_type,
    }
    if valid_through:
        metadata["valid_through"] = valid_through
    if org_id is not None:
        metadata["org_id"] = org_id

    return RawListing(
        source="aviation_job_search",
        source_listing_id=listing_id,
        url=url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw=employment_type or "unknown",
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class AviationJobSearchAdapter(SourceAdapter):
    """Adapter for aviationjobsearch.com.

    Strategy: Sitemap-driven + LD+JSON parsing of individual job-detail pages.
    See module docstring for full rationale.
    Crawl-delay: 3 s between every HTTP request (robots.txt + team decision).
    Aggregator: True — board aggregates employer ATS feeds.
    """

    name = "aviation_job_search"

    def __init__(self, crawl_delay: int = 3, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch aviation management / PM job listings via sitemap + LD+JSON.

        1. Fetch jobs.xml sitemap → filter relevant URLs.
        2. Fetch each job-detail page → parse schema.org/JobPosting LD+JSON.
        3. Apply *since* filter client-side on posted_at.

        Returns [] on any unrecoverable error — never raises.
        """
        listings: list[RawListing] = []
        try:
            async with httpx.AsyncClient(
                headers=_BROWSER_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                job_urls = await self._fetch_sitemap(client)
                if not job_urls:
                    logger.warning(
                        "Aviation Job Search: sitemap returned 0 relevant job URLs."
                    )
                    return listings

                logger.info(
                    "Aviation Job Search: %d relevant job URL(s) from sitemap.",
                    len(job_urls),
                )

                for idx, url in enumerate(job_urls):
                    raw = await self._fetch_job_page(client, url)
                    if raw is not None:
                        if since and raw.posted_at and raw.posted_at < since:
                            continue
                        listings.append(raw)
                    if idx < len(job_urls) - 1:
                        await asyncio.sleep(_PAGE_DELAY_SECONDS)

        except Exception:
            logger.exception(
                "Aviation Job Search fetch failed — returning %d partial listing(s).",
                len(listings),
            )
            return listings

        logger.info(
            "Aviation Job Search: fetched %d listing(s) (since=%s).",
            len(listings),
            since,
        )
        return listings

    async def _fetch_sitemap(self, client: httpx.AsyncClient) -> list[str]:
        """Fetch jobs.xml sitemap and return filtered job-detail URLs."""
        try:
            resp = await client.get(_SITEMAP_URL)
            resp.raise_for_status()
        except Exception:
            logger.exception("Aviation Job Search: failed to fetch sitemap.")
            return []

        urls: list[str] = []
        for m in _SITEMAP_RE.finditer(resp.text):
            loc = m.group(1).strip()
            if _is_relevant_url(loc):
                urls.append(loc)
        return urls[:_MAX_JOBS]

    async def _fetch_job_page(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> RawListing | None:
        """Fetch one job-detail page and parse its LD+JSON. Returns None on error."""
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Aviation Job Search HTTP error (url=%s, status=%s).",
                url,
                exc.response.status_code,
            )
            return None
        except Exception:
            logger.exception(
                "Aviation Job Search request failed (url=%s).", url
            )
            return None

        job = _parse_ldjson_job(resp.text)
        if job is None:
            logger.warning(
                "Aviation Job Search: no JobPosting LD+JSON found at %s.", url
            )
            return None
        return _map_ldjson_job(job, url)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio as _asyncio
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    _adapter = AviationJobSearchAdapter()
    _results = _asyncio.run(_adapter.fetch())
    print(f"Fetched {len(_results)} listing(s) from Aviation Job Search.")
    for _r in _results[:5]:
        print(
            f"  [{_r.source_listing_id}] {_r.title!r}"
            f" | {_r.employer} | {_r.location_raw}"
            f" | {_r.posted_at} | {_r.contract_type_raw}"
        )
