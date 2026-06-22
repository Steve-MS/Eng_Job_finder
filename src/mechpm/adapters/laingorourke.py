"""Laing O'Rourke Careers adapter — JSON-LD scrape via sitemap.

Overview
--------
Laing O'Rourke's careers site (careers.laingorourke.com) publishes a
sitemap at ``/sitemap.xml`` listing all job URLs with ``<lastmod>`` dates.
Each job page embeds a ``JobPosting`` JSON-LD block in a
``<script type="application/ld+json">`` tag.

Discovery (2026-06-22)
-----------------------
- Sitemap: GET https://careers.laingorourke.com/sitemap.xml → 254 URL entries
- Job URL format: /jobs/{slug}
- JSON-LD @type: "JobPosting" confirmed on all sampled pages
- Confirmed fields: title, description, datePosted, employmentType,
  validThrough, hiringOrganization, identifier, jobLocation
- baseSalary absent in probed pages; extracted when present
- AWS WAF challenge script present but does not block normal GET requests
- robots.txt: Crawl-delay: 5; disallows /api/ and /me/

JSON-LD field map (confirmed 2026-06-22)
-----------------------------------------
  title                           → title
  identifier.value (hash)         → source_listing_id (fallback: URL slug)
  URL                             → url
  datePosted                      → posted_at
  description                     → description_raw
  jobLocation[0].address          → location_raw
  baseSalary                      → salary_raw (when present)
  employmentType                  → contract_type_raw
  hiringOrganization.name         → employer

Fetch strategy
--------------
1. Fetch sitemap XML → extract all /jobs/ URLs with lastmod dates.
2. Sort by lastmod descending (most recent first); cap at max_jobs.
3. For each URL, fetch the HTML page, extract JobPosting JSON-LD.
4. Map JSON-LD fields to RawListing.
5. Filter by keywords_list (title or description contains any keyword).
6. Filter by since on datePosted.
7. Deduplicate by URL.
8. Respect crawl_delay seconds between every page fetch.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.laingorourke")

_SOURCE_NAME = "laingorourke"
_DEFAULT_BASE_URL = "https://careers.laingorourke.com"
_SITEMAP_PATH = "/sitemap.xml"
_EMPLOYER = "Laing O'Rourke"
_DEFAULT_MAX_JOBS = 100
_DEFAULT_CRAWL_DELAY = 5
_REQUEST_TIMEOUT = 30.0

_DEFAULT_KEYWORDS: list[str] = [
    "project manager",
    "project director",
    "programme manager",
    "quantity surveyor",
    "risk manager",
    "commercial manager",
    "operations director",
    "project planner",
]

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

_XML_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_sitemap(xml_text: str, base_url: str) -> list[tuple[str, date | None]]:
    """Parse sitemap XML and return (url, lastmod) pairs for /jobs/ paths.

    Filters to URLs containing ``/jobs/`` only.  Sorts by lastmod descending
    (most-recent first); entries with no lastmod date sort last.

    Args:
        xml_text: Raw sitemap XML content.
        base_url: Site base URL (used for namespace resolution; not filtered on).

    Returns:
        List of ``(url, lastmod_date_or_None)`` tuples, sorted newest-first.
        Returns ``[]`` on any parse error or empty input.
    """
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("laingorourke: failed to parse sitemap XML")
        return []

    results: list[tuple[str, date | None]] = []

    for url_el in root.findall(f"{{{_XML_NS}}}url"):
        loc_el = url_el.find(f"{{{_XML_NS}}}loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        if "/jobs/" not in loc:
            continue

        lastmod: date | None = None
        lastmod_el = url_el.find(f"{{{_XML_NS}}}lastmod")
        if lastmod_el is not None and lastmod_el.text:
            try:
                lastmod = date.fromisoformat(lastmod_el.text.strip()[:10])
            except ValueError:
                pass

        results.append((loc, lastmod))

    # Sort: entries with lastmod newest-first; entries without lastmod last.
    results.sort(
        key=lambda x: x[1] if x[1] is not None else date.min,
        reverse=True,
    )
    return results


def _extract_job_jsonld(html: str) -> dict[str, Any] | None:
    """Extract the first JobPosting JSON-LD object from an HTML page.

    Scans all ``<script type="application/ld+json">`` blocks and returns the
    first dict whose ``@type`` equals ``"JobPosting"``.

    Args:
        html: Raw HTML page content.

    Returns:
        Parsed JSON-LD dict or ``None`` when none is found.
    """
    if not html:
        return None
    try:
        tree = HTMLParser(html)
    except Exception:
        return None

    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text(strip=True)
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
            return obj

    return None


def _build_location_raw(jsonld: dict[str, Any]) -> str | None:
    """Build a location string from ``jobLocation`` in a JobPosting JSON-LD.

    Combines ``addressLocality``, ``addressRegion``, and ``addressCountry``
    from the first jobLocation entry.  Skips blank fields.

    Args:
        jsonld: Parsed JobPosting JSON-LD dict.

    Returns:
        Location string (e.g. ``"Leiston, GB"``) or ``None``.
    """
    job_location = jsonld.get("jobLocation")
    if not job_location:
        return None
    loc_obj = job_location[0] if isinstance(job_location, list) else job_location
    if not isinstance(loc_obj, dict):
        return None
    address = loc_obj.get("address", {})
    if not isinstance(address, dict):
        return None

    parts = [
        v.strip()
        for key in ("addressLocality", "addressRegion", "addressCountry")
        if (v := address.get(key, "")) and v.strip()
    ]
    return ", ".join(parts) if parts else None


def _build_salary_raw(jsonld: dict[str, Any]) -> str | None:
    """Extract a human-readable salary string from ``baseSalary`` in JSON-LD.

    Handles string, flat dict, and structured ``MonetaryAmountDistribution``
    shapes.  Returns ``None`` when ``baseSalary`` is absent.

    Args:
        jsonld: Parsed JobPosting JSON-LD dict.

    Returns:
        Salary string or ``None``.
    """
    base_salary = jsonld.get("baseSalary")
    if not base_salary:
        return None
    if isinstance(base_salary, str):
        return base_salary.strip() or None
    if isinstance(base_salary, dict):
        currency = base_salary.get("currency", "")
        value = base_salary.get("value")
        if value is None:
            return None
        if isinstance(value, dict):
            min_val = value.get("minValue")
            max_val = value.get("maxValue")
            unit = value.get("unitText", "")
            if min_val is not None and max_val is not None:
                return f"{currency} {min_val}–{max_val} {unit}".strip()
            if min_val is not None:
                return f"{currency} {min_val} {unit}".strip()
        return f"{currency} {value}".strip() or None
    return None


def _build_source_listing_id(jsonld: dict[str, Any], url: str) -> str:
    """Build a stable ``source_listing_id`` from JSON-LD identifier or URL slug.

    Prefers ``identifier.value`` (the ATS hash) when non-empty.  Falls back
    to the last path segment of the URL.

    Args:
        jsonld: Parsed JobPosting JSON-LD dict.
        url: Job page URL.

    Returns:
        Non-empty identifier string.
    """
    identifier = jsonld.get("identifier")
    if isinstance(identifier, dict):
        val = str(identifier.get("value", "")).strip()
        if val:
            return val
    path = url.rstrip("/")
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse an ISO 8601 date string to a timezone-aware ``datetime``.

    Handles ``YYYY-MM-DDTHH:MM:SSZ``, ``YYYY-MM-DDTHH:MM:SS+HH:MM``,
    and ``YYYY-MM-DD`` formats.

    Args:
        date_str: ISO 8601 date string or ``None``.

    Returns:
        UTC-aware ``datetime`` or ``None`` on failure.
    """
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _matches_keywords(
    title: str, description: str | None, keywords: list[str]
) -> bool:
    """Return ``True`` if title or description contains any keyword.

    Comparison is case-insensitive.  When ``keywords`` is empty, the filter
    is bypassed and ``True`` is returned.

    Args:
        title: Job title string.
        description: Raw description (may contain HTML markup).
        keywords: List of keyword strings.

    Returns:
        ``True`` when at least one keyword matches, or when ``keywords`` is empty.
    """
    if not keywords:
        return True
    haystack = (title + " " + (description or "")).lower()
    return any(kw.lower() in haystack for kw in keywords)


def _jsonld_to_raw_listing(jsonld: dict[str, Any], url: str) -> RawListing | None:
    """Map a ``JobPosting`` JSON-LD dict to a ``RawListing``.

    Returns ``None`` when the required ``title`` field is absent or empty.

    Args:
        jsonld: Parsed JobPosting JSON-LD dict.
        url: Canonical job page URL.

    Returns:
        ``RawListing`` instance or ``None``.
    """
    title = (jsonld.get("title") or "").strip()
    if not title:
        return None

    hiring_org = jsonld.get("hiringOrganization") or {}
    employer: str = _EMPLOYER
    if isinstance(hiring_org, dict):
        name = (hiring_org.get("name") or "").strip()
        if name:
            employer = name

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=_build_source_listing_id(jsonld, url),
        url=url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=_build_location_raw(jsonld),
        description_raw=jsonld.get("description") or None,
        posted_at=_parse_date(jsonld.get("datePosted")),
        salary_raw=_build_salary_raw(jsonld),
        contract_type_raw=jsonld.get("employmentType") or None,
        metadata={
            "validThrough": jsonld.get("validThrough"),
        },
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class LaingORourkeAdapter(SourceAdapter):
    """Adapter for Laing O'Rourke Careers — JSON-LD scrape via sitemap.

    Sitemap strategy
    ~~~~~~~~~~~~~~~~
    Fetches ``/sitemap.xml`` to enumerate all job URLs with ``lastmod``
    dates.  Sorts by ``lastmod`` descending so the most-recent postings are
    fetched first.  Caps at ``max_jobs`` to respect the 5-second crawl-delay
    budget (~8 min for 100 jobs).

    JSON-LD extraction
    ~~~~~~~~~~~~~~~~~~
    Each job page contains a ``<script type="application/ld+json">`` block
    with ``@type: "JobPosting"``.  Confirmed fields (2026-06-22): title,
    description, datePosted, employmentType, validThrough,
    hiringOrganization, identifier, jobLocation.

    Keyword filter
    ~~~~~~~~~~~~~~
    Applied client-side on title + description text.  With an empty
    ``keywords_list`` the filter is bypassed.

    Graceful errors
    ~~~~~~~~~~~~~~~
    Individual page failures are logged and skipped.  The run continues
    to the next URL.  Returns ``[]`` on total failure; never crashes the
    pipeline.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        crawl_delay: int = _DEFAULT_CRAWL_DELAY,
        keywords_list: list[str] | None = None,
        max_jobs: int = _DEFAULT_MAX_JOBS,
        **kwargs: object,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.crawl_delay = crawl_delay
        self.keywords_list: list[str] = keywords_list or list(_DEFAULT_KEYWORDS)
        self.max_jobs = max_jobs

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Scrape Laing O'Rourke Careers for job listings.

        Steps:
        1. Fetches ``/sitemap.xml`` → sorted list of job URLs (newest first).
        2. Fetches up to ``max_jobs`` individual job pages (with crawl delay).
        3. Extracts ``JobPosting`` JSON-LD from each page.
        4. Applies ``since`` filter on ``datePosted``.
        5. Applies ``keywords_list`` filter on title + description.
        6. Deduplicates by URL.

        Returns ``[]`` and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                sitemap_url = self.base_url + _SITEMAP_PATH
                job_urls = await self._fetch_sitemap(client, sitemap_url)
                if not job_urls:
                    logger.warning(
                        "laingorourke: sitemap returned no job URLs — aborting."
                    )
                    return []

                logger.info(
                    "laingorourke: sitemap yielded %d job URL(s); "
                    "capping at max_jobs=%d.",
                    len(job_urls),
                    self.max_jobs,
                )

                for idx, (url, _lastmod) in enumerate(job_urls[: self.max_jobs]):
                    if idx > 0:
                        await asyncio.sleep(self.crawl_delay)

                    listing = await self._fetch_job(client, url)
                    if listing is None:
                        continue

                    if since and listing.posted_at and listing.posted_at < since:
                        continue

                    if not _matches_keywords(
                        listing.title,
                        listing.description_raw,
                        self.keywords_list,
                    ):
                        continue

                    if listing.url not in seen:
                        seen[listing.url] = listing

        except Exception:
            logger.exception(
                "laingorourke: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "laingorourke: fetched %d unique listing(s) "
            "(max_jobs=%d, since=%s).",
            len(listings),
            self.max_jobs,
            since,
        )
        return listings

    async def _fetch_sitemap(
        self,
        client: httpx.AsyncClient,
        sitemap_url: str,
    ) -> list[tuple[str, date | None]]:
        """Fetch and parse ``/sitemap.xml`` with retry for transient failures.

        Returns a sorted list of ``(url, lastmod)`` tuples for ``/jobs/``
        paths.  Returns ``[]`` on any unrecoverable network or parse error.
        """
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = await client.get(sitemap_url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "laingorourke: HTTP %s fetching sitemap (%s), attempt %d/%d.",
                    exc.response.status_code,
                    sitemap_url,
                    attempt + 1,
                    max_attempts,
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                return []
            except Exception:
                logger.exception(
                    "laingorourke: request failed fetching sitemap (%s), attempt %d/%d.",
                    sitemap_url,
                    attempt + 1,
                    max_attempts,
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                return []

            result = _parse_sitemap(response.text, self.base_url)
            if result:
                return result

            # Parse succeeded but returned empty — may be a WAF challenge page
            logger.warning(
                "laingorourke: sitemap parsed but yielded 0 job URLs "
                "(attempt %d/%d) — retrying.",
                attempt + 1,
                max_attempts,
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(2 ** (attempt + 1))

        return []

    async def _fetch_job(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> RawListing | None:
        """Fetch a single job page and return a ``RawListing``.

        Returns ``None`` on any HTTP error, network failure, or when no
        ``JobPosting`` JSON-LD is found on the page.
        """
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "laingorourke: HTTP %s fetching job page (%s).",
                exc.response.status_code,
                url,
            )
            return None
        except Exception:
            logger.exception(
                "laingorourke: request failed for job page (%s).",
                url,
            )
            return None

        jsonld = _extract_job_jsonld(response.text)
        if jsonld is None:
            logger.debug(
                "laingorourke: no JobPosting JSON-LD found at %s.", url
            )
            return None

        return _jsonld_to_raw_listing(jsonld, url)
