"""CIOB Jobs adapter — RSS feed.

Overview
--------
CIOB Jobs (ciobjobs.com) is the official job board of the Chartered Institute
of Building.  The site exposes a standard WordPress RSS 2.0 feed at
``https://ciobjobs.com/feed/`` that publishes recent job postings.

Discovery (2026-06-19)
-----------------------
- Feed URL: https://ciobjobs.com/feed/ (RSS 2.0, WordPress)
- No authentication required; no Cloudflare or bot-management blocking observed.
- robots.txt: standard WordPress robots.txt — ``/feed/`` paths are allowed.
- Job detail pages carry JSON-LD structured data (JobPosting schema); not used
  here as the RSS feed provides sufficient fields for ingestion.
- Feed is a rolling window of recent postings (no explicit pagination).

Field map (RSS item → RawListing)
-----------------------------------
  <title>                         → title
  <link>                          → url
  <pubDate>                       → posted_at (RFC 2822 → UTC-aware datetime)
  <content:encoded> or            → description_raw (full HTML preferred)
    <description>
  <guid>                          → metadata.guid
  URL slug (last path segment)    → source_listing_id
  Location extracted from HTML    → location_raw  (best-effort regex)
  Salary extracted from HTML      → salary_raw    (best-effort regex)
  Employer extracted from HTML    → employer      (best-effort regex)

Extraction strategy
-------------------
  Location, salary, and employer are not available as dedicated RSS elements in
  the standard WordPress RSS format.  The adapter applies lightweight regex
  extraction against the plain-text content (HTML tags stripped).  All extracted
  fields are optional — None is an acceptable value.

Fetch strategy
--------------
  Single GET request per run; the feed covers the most recent postings.
  The ``since`` filter is applied after parsing.  ``crawl_delay`` is respected
  by the orchestrator between adapters.
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.ciob_jobs")

_SOURCE_NAME = "ciob_jobs"
_DEFAULT_FEED_URL = "https://ciobjobs.com/feed/"
_REQUEST_TIMEOUT = 30.0

# RSS 2.0 namespace for <content:encoded>
_NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"

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


def _parse_pub_date(raw: str | None) -> datetime | None:
    """Parse an RSS pubDate string to a UTC-aware datetime.

    Primary format: RFC 2822 (``"Thu, 19 Jun 2026 08:00:00 +0000"``).
    Falls back to ISO 8601 variants for defensive robustness.
    Returns None on any parse failure.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    # Primary: RFC 2822 (standard RSS pubDate format)
    try:
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Fallback: ISO 8601 variants (defensive; not expected from CIOB RSS)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
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


def _extract_id_from_url(url: str) -> str:
    """Derive a stable listing ID from the job URL.

    Prefers the last non-empty path segment (URL slug), e.g.
    ``https://ciobjobs.com/job/senior-project-manager/`` → ``"senior-project-manager"``.
    Falls back to an MD5 hex-digest prefix when no slug is available.
    """
    if not url:
        return hashlib.md5(b"").hexdigest()[:16]
    path = urlparse(url).path.rstrip("/")
    if path:
        slug = path.split("/")[-1]
        if slug:
            return slug
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def _extract_field(pattern: re.Pattern[str], text: str) -> str | None:
    """Return the first capture group of ``pattern`` in ``text``, stripped.

    Returns None when there is no match or the capture is empty/whitespace.
    """
    m = pattern.search(text)
    if not m:
        return None
    value = m.group(1).strip()
    return value if value else None


def _parse_item(item: ET.Element) -> RawListing | None:
    """Map one RSS ``<item>`` element to a RawListing.

    Returns None when the item is missing a title or link.
    """
    title = (item.findtext("title") or "").strip()
    if not title:
        return None

    url = (item.findtext("link") or "").strip()
    if not url:
        return None

    pub_date_raw = item.findtext("pubDate")
    posted_at = _parse_pub_date(pub_date_raw)

    # Prefer <content:encoded> (full HTML); fall back to <description> (excerpt)
    content = (
        item.findtext(f"{{{_NS_CONTENT}}}encoded")
        or item.findtext("description")
        or ""
    )
    description_raw = content.strip() or None

    # Best-effort structured field extraction from plain text
    plain_text = _strip_html(content) if content else ""
    location_raw = _extract_field(_RE_LOCATION, plain_text)
    salary_raw = _extract_field(_RE_SALARY, plain_text)
    employer = _extract_field(_RE_EMPLOYER, plain_text)

    source_listing_id = _extract_id_from_url(url)

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=source_listing_id,
        url=url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw=None,  # not available in the RSS feed
        metadata={
            "guid": (item.findtext("guid") or "").strip() or None,
            "pub_date_raw": pub_date_raw,
        },
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class CiobJobsAdapter(SourceAdapter):
    """Adapter for CIOB Jobs via the WordPress RSS 2.0 feed.

    Fetches ``feed_url`` with a single GET request, parses all ``<item>``
    elements, and maps them to RawListing records.  No pagination is needed —
    the feed is a rolling window of recent postings.  The ``since`` filter is
    applied after parsing.

    robots.txt: permissive; no bot-management protection observed (2026-06-19).
    A single request is made per run; ``crawl_delay`` is respected by the
    orchestrator between adapters.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        feed_url: str = _DEFAULT_FEED_URL,
        crawl_delay: int = 2,
        keywords_list: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        self.feed_url = feed_url
        self.crawl_delay = crawl_delay
        self.keywords_list = keywords_list or []

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch and parse the CIOB Jobs RSS feed.

        - GETs ``self.feed_url`` using httpx with a polite User-Agent.
        - Parses the RSS XML with ``xml.etree.ElementTree``.
        - Applies the ``since`` filter on ``posted_at`` (entries with no
          ``posted_at`` are always included).
        - Returns [] and logs a warning on any unrecoverable error.
        """
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                try:
                    response = await client.get(self.feed_url)
                except httpx.RequestError as exc:
                    logger.warning("ciob_jobs: request error fetching feed: %s", exc)
                    return []

                if response.status_code != 200:
                    logger.warning(
                        "ciob_jobs: HTTP %d from feed URL %s",
                        response.status_code,
                        self.feed_url,
                    )
                    return []

                return self._parse_feed(response.text, since=since)

        except Exception:
            logger.exception("ciob_jobs: unexpected error in fetch() — returning [].")
            return []

    def _parse_feed(
        self,
        xml_text: str,
        since: datetime | None = None,
    ) -> list[RawListing]:
        """Parse RSS XML text and return filtered, deduplicated RawListing records.

        Returns [] on XML parse failure (logs a warning).
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("ciob_jobs: XML parse error: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("ciob_jobs: no <channel> element in RSS feed")
            return []

        listings: list[RawListing] = []
        seen_ids: set[str] = set()

        for item_el in channel.findall("item"):
            listing = _parse_item(item_el)
            if listing is None:
                continue
            if since and listing.posted_at and listing.posted_at < since:
                continue
            lid = listing.source_listing_id
            if lid in seen_ids:
                continue
            seen_ids.add(lid)
            listings.append(listing)

        logger.info(
            "ciob_jobs: parsed %d listing(s) from RSS feed (since=%s).",
            len(listings),
            since,
        )
        return listings
