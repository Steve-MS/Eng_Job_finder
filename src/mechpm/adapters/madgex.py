"""Madgex job board platform adapter — RICS Recruit + ICE Recruit.

Overview
--------
Both RICS Recruit (ricsrecruit.com) and ICE Recruit (icerecruit.com) run on
the Madgex job board platform.  This shared adapter handles both sites via
config — only ``base_url`` and ``source_name`` differ between instances.

Discovery (live recon 2026-06-22)
----------------------------------
- RSS endpoint: GET {base_url}/jobsrss/?countrycode={country_code}
- Keyword filter: &Keywords={keyword}
- Response: RSS 2.0 with OpenSearch 1.1 extensions
- opensearch:totalResults in <channel> (521 for RICS, 285 for ICE live)
- Items per page: counted from first page response
- Pagination: &page=2, &page=3, ...
- No authentication required; no bot-management blocking observed

RSS item structure (live confirmed)
-------------------------------------
  <title>Agency Name: Job Title</title>
  <description>
    £400 - £500 per day:

    Agency Name:
    Description text...
    Location, Region
  </description>
  <link>https://{base}/job/{id}/{slug}/?TrackID=124&amp;utm_source=rss...</link>
  <pubDate>Mon, 22 Jun 2026 12:28:00 +0000</pubDate>
  <guid isPermaLink="true">same URL as link</guid>

Field map (RSS item → RawListing)
----------------------------------
  title (split on ": ")  → title (job part), agency (part before ": ")
  link (tracking stripped) → url
  link (path) or guid    → source_listing_id  (numeric job ID)
  pubDate                → posted_at  (RFC 2822 → UTC datetime)
  description (first line) → salary_raw  (if contains currency/rate)
  description (last line)  → location_raw
  description (body)       → description_raw  (raw text as stored)
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.madgex")

_REQUEST_TIMEOUT = 30.0
_PAGE_DELAY_SECONDS = 2
_DEFAULT_COUNTRY_CODE = "GB"
_DEFAULT_MAX_PAGES = 20

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
}

# OpenSearch 1.1 namespace
_NS_OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"
_OS_TOTAL = f"{{{_NS_OPENSEARCH}}}totalResults"
_OS_ITEMS_PER_PAGE = f"{{{_NS_OPENSEARCH}}}itemsPerPage"

# Numeric job ID from URL path: /job/{id}/
_RE_JOB_ID = re.compile(r"/job/(\d+)/")

# Remove HTML tags
_RE_STRIP_HTML = re.compile(r"<[^>]+>")

# Tracking parameters stripped from job URLs
_TRACKING_PARAMS = frozenset({"TrackID", "utm_source", "utm_medium", "utm_campaign"})

# Salary indicators in description first line
_RE_SALARY_LINE = re.compile(
    r"[£$€]|(?:\bper\s+(?:day|week|annum|year|hour|month)\b)"
    r"|(?:\bp(?:/|\.)a\b)"
    r"|(?:\bk\b)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure helpers (module-level so tests can call them directly)
# ---------------------------------------------------------------------------


def _strip_html(html: str) -> str:
    """Remove HTML tags; collapse excess whitespace."""
    return _RE_STRIP_HTML.sub(" ", html).strip()


def _parse_rfc2822_date(raw: str | None) -> datetime | None:
    """Parse an RFC 2822 pubDate string to a UTC-aware datetime.

    e.g. ``"Mon, 22 Jun 2026 12:28:00 +0000"``
    Returns None on any parse failure.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(timezone.utc)
    except Exception:
        logger.debug("madgex: could not parse date %r", raw)
        return None


def _extract_id_from_url(url: str) -> str | None:
    """Extract the numeric job ID from a Madgex job URL.

    e.g. ``https://www.ricsrecruit.com/job/254176/job-slug/?TrackID=124``
    → ``"254176"``

    Returns None when no ID is found.
    """
    if not url:
        return None
    m = _RE_JOB_ID.search(url)
    if not m:
        return None
    return m.group(1)


def _clean_url(url: str) -> str:
    """Strip UTM tracking and TrackID parameters from a Madgex job URL.

    Preserves all other query parameters (e.g. countrycode).
    Returns the original string unchanged when it cannot be parsed.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in qs.items() if k not in _TRACKING_PARAMS}
        new_query = urlencode(cleaned, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def _parse_title(raw_title: str) -> tuple[str, str | None]:
    """Split a Madgex RSS title into ``(job_title, agency)``.

    Madgex title format: ``"Agency Name: Job Title"``
    Returns ``(raw_title.strip(), None)`` when no ``": "`` separator is found.
    When the first occurrence of ``": "`` is used, anything before it becomes
    the agency and the remainder becomes the job title.
    """
    if not raw_title:
        return raw_title, None
    idx = raw_title.find(": ")
    if idx < 0:
        return raw_title.strip(), None
    agency = raw_title[:idx].strip()
    job_title = raw_title[idx + 2:].strip()
    return job_title, agency if agency else None


def _parse_description(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """Parse a Madgex RSS description block.

    Madgex description format (plain text)::

        £400 - £500 per day:

        Agency Name:
        Description body text...
        Location, Region

    Extraction logic:
    - **salary_raw**: first non-empty line, if it matches a currency/rate
      pattern or ends with ``":"``.  Trailing ``":"`` is stripped.
    - **location_raw**: last non-empty line (after removing the salary line
      and any agency-header lines).
    - **description_clean**: remaining body lines joined with a space.

    All three return values may be ``None`` when absent.

    Args:
        raw: Raw text from the ``<description>`` element (may contain HTML).

    Returns:
        ``(salary_raw, location_raw, description_clean)``
    """
    if not raw:
        return None, None, None

    text = _strip_html(raw)
    lines = [ln.strip() for ln in text.splitlines()]
    # Drop leading/trailing blank lines
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    if not lines:
        return None, None, None

    salary_raw: str | None = None
    remaining = list(lines)

    # First non-empty line may be a salary line
    first = remaining[0]
    if first and (_RE_SALARY_LINE.search(first) or first.endswith(":")):
        salary_raw = first.rstrip(":").strip() or None
        remaining = remaining[1:]
        # Skip blank line following salary
        while remaining and not remaining[0]:
            remaining.pop(0)

    # Skip agency-header lines (lines that end with ":" and look like a name)
    while remaining and remaining[0].endswith(":"):
        remaining.pop(0)
        # Skip blank line following header
        while remaining and not remaining[0]:
            remaining.pop(0)

    if not remaining:
        return salary_raw, None, None

    # Last non-empty line is the location
    location_raw = remaining[-1] if remaining[-1] else None
    body_lines = remaining[:-1] if len(remaining) > 1 else []

    # Collapse body into a single string
    description_clean = " ".join(ln for ln in body_lines if ln) or None

    return salary_raw, location_raw, description_clean


def _parse_rss_page(
    xml_text: str,
    source_name: str,
) -> tuple[list[RawListing], int | None, int | None]:
    """Parse one page of Madgex RSS XML.

    Args:
        xml_text:    Raw XML text of the RSS response.
        source_name: Name of the source (used in log messages and RawListing.source).

    Returns:
        ``(listings, total_results, items_per_page)``

        - ``total_results``: value of ``<opensearch:totalResults>`` or ``None``.
        - ``items_per_page``: value of ``<opensearch:itemsPerPage>`` or ``None``.
        - On XML parse error: ``([], None, None)``.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("madgex[%s]: XML parse error: %s", source_name, exc)
        return [], None, None

    channel = root.find("channel")
    if channel is None:
        logger.warning("madgex[%s]: no <channel> in RSS document", source_name)
        return [], None, None

    total_results: int | None = None
    items_per_page: int | None = None

    total_el = channel.find(_OS_TOTAL)
    if total_el is not None and total_el.text:
        try:
            total_results = int(total_el.text.strip())
        except ValueError:
            pass

    ipp_el = channel.find(_OS_ITEMS_PER_PAGE)
    if ipp_el is not None and ipp_el.text:
        try:
            items_per_page = int(ipp_el.text.strip())
        except ValueError:
            pass

    listings: list[RawListing] = []
    for item_el in channel.findall("item"):
        listing = _item_to_raw_listing(item_el, source_name)
        if listing is not None:
            listings.append(listing)

    return listings, total_results, items_per_page


def _item_to_raw_listing(
    item_el: ET.Element,
    source_name: str,
) -> RawListing | None:
    """Map one RSS ``<item>`` element to a :class:`RawListing`.

    Returns ``None`` when required fields (title, link, or job ID) are absent.
    """
    title_el = item_el.find("title")
    link_el = item_el.find("link")

    raw_title = (title_el.text or "").strip() if title_el is not None else ""
    raw_link = (link_el.text or "").strip() if link_el is not None else ""

    if not raw_title or not raw_link:
        return None

    job_title, agency = _parse_title(raw_title)
    if not job_title:
        return None

    # Try to extract numeric ID from the link URL first
    source_listing_id = _extract_id_from_url(raw_link)
    if not source_listing_id:
        # Fall back to guid element
        guid_el = item_el.find("guid")
        if guid_el is not None and guid_el.text:
            source_listing_id = _extract_id_from_url(guid_el.text.strip())
    if not source_listing_id:
        return None

    # Clean tracking parameters from URL
    url = _clean_url(raw_link)

    pubdate_el = item_el.find("pubDate")
    posted_at = _parse_rfc2822_date(
        pubdate_el.text if pubdate_el is not None else None
    )

    desc_el = item_el.find("description")
    desc_raw = (desc_el.text or "").strip() if desc_el is not None else ""

    salary_raw, location_raw, _desc_clean = _parse_description(desc_raw)

    return RawListing(
        source=source_name,
        source_listing_id=source_listing_id,
        url=url,
        title=job_title,
        employer=None,
        agency=agency,
        location_raw=location_raw,
        description_raw=desc_raw if desc_raw else None,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw=None,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class MadgexAdapter(SourceAdapter):
    """Adapter for Madgex job board platform (RICS Recruit, ICE Recruit).

    A single class handles multiple Madgex-powered sites via config.
    Only ``base_url`` and ``source_name`` differ between instances.

    RSS feed URL pattern::

        {base_url}/jobsrss/?countrycode={country_code}[&Keywords={keyword}][&page=N]

    For each entry in ``keywords_list`` the adapter fetches one or more pages
    of RSS results, deduplicating by ``source_listing_id`` across keywords and
    pages.  If ``keywords_list`` is empty, a single un-filtered fetch is made
    (returns all jobs for the country).

    Pagination is driven by ``<opensearch:totalResults>`` vs items returned on
    page 1 (or ``<opensearch:itemsPerPage>`` when present).  The ``max_pages``
    cap prevents runaway loops.

    Args:
        base_url:      Base URL of the Madgex site, e.g. ``"https://www.ricsrecruit.com"``.
        source_name:   Logical name used in ``RawListing.source``, e.g. ``"rics_recruit"``.
        crawl_delay:   Seconds the orchestrator sleeps between adapters (default 3).
        keywords_list: List of keyword strings to search.  Empty → fetch all.
        country_code:  ISO country code passed to the RSS endpoint (default ``"GB"``).
        max_pages:     Per-keyword pagination cap (default 20).
    """

    def __init__(
        self,
        base_url: str,
        source_name: str,
        crawl_delay: int = 3,
        keywords_list: list[str] | None = None,
        country_code: str = _DEFAULT_COUNTRY_CODE,
        max_pages: int = _DEFAULT_MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.name = source_name
        self.base_url = base_url.rstrip("/")
        self.crawl_delay = crawl_delay
        self.keywords_list: list[str] = keywords_list or []
        self.country_code = country_code
        self.max_pages = max_pages

    def _build_rss_url(self, keyword: str | None = None, page: int = 1) -> str:
        """Build the RSS feed URL for a given keyword and page number."""
        url = f"{self.base_url}/jobsrss/?countrycode={self.country_code}"
        if keyword:
            url += f"&Keywords={quote_plus(keyword)}"
        if page > 1:
            url += f"&page={page}"
        return url

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch all Madgex RSS listings, deduplicating across keywords and pages.

        Iterates ``keywords_list`` (or fetches once without a keyword when the
        list is empty).  Deduplicates by ``source_listing_id``.  Applies the
        ``since`` filter on ``posted_at`` (listings without a date are always
        included).

        Returns ``[]`` and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}
        queries: list[str | None] = self.keywords_list if self.keywords_list else [None]

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for i, keyword in enumerate(queries):
                    if i > 0:
                        await asyncio.sleep(_PAGE_DELAY_SECONDS)
                    await self._fetch_keyword(client, keyword, since, seen)
        except Exception:
            logger.exception(
                "madgex[%s]: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                self.name,
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "madgex[%s]: fetched %d unique listing(s) "
            "(keywords=%d, since=%s).",
            self.name,
            len(listings),
            len(queries),
            since,
        )
        return listings

    async def _fetch_keyword(
        self,
        client: httpx.AsyncClient,
        keyword: str | None,
        since: datetime | None,
        seen: dict[str, RawListing],
    ) -> None:
        """Fetch all pages for one keyword, adding new listings to ``seen``."""
        items_on_first_page: int | None = None
        total_results: int | None = None

        for page in range(1, self.max_pages + 1):
            if page > 1:
                await asyncio.sleep(_PAGE_DELAY_SECONDS)

            url = self._build_rss_url(keyword, page)
            xml_text = await self._fetch_page_xml(client, url, keyword)
            if xml_text is None:
                break

            listings, page_total, page_ipp = _parse_rss_page(xml_text, self.name)

            if page == 1:
                total_results = page_total
                items_on_first_page = len(listings)

            for listing in listings:
                if since and listing.posted_at and listing.posted_at < since:
                    continue
                lid = listing.source_listing_id
                if lid and lid not in seen:
                    seen[lid] = listing

            logger.debug(
                "madgex[%s]: keyword=%r page=%d → %d items, "
                "%d unique total (total_results=%s).",
                self.name,
                keyword,
                page,
                len(listings),
                len(seen),
                total_results,
            )

            if not listings:
                break

            # Determine effective page size to decide whether to paginate
            # Prefer opensearch:itemsPerPage; fall back to first-page count.
            effective_page_size = page_ipp or items_on_first_page or len(listings)
            if not effective_page_size:
                break

            if total_results is not None:
                # Ceiling division: number of pages needed
                pages_needed = (total_results + effective_page_size - 1) // effective_page_size
                if page >= pages_needed:
                    break
            else:
                # No totalResults: stop on a partial page
                if len(listings) < effective_page_size:
                    break

    async def _fetch_page_xml(
        self,
        client: httpx.AsyncClient,
        url: str,
        keyword: str | None,
    ) -> str | None:
        """Fetch one RSS page; return raw XML text or ``None`` on failure."""
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning(
                "madgex[%s]: request error for keyword=%r: %s",
                self.name,
                keyword,
                exc,
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "madgex[%s]: HTTP %d for keyword=%r url=%s",
                self.name,
                response.status_code,
                keyword,
                url,
            )
            return None

        return response.text
