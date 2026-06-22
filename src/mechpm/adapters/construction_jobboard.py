"""ConstructionJobBoard adapter — Smart Job Board HTML scrape.

Overview
--------
ConstructionJobBoard.co.uk (Smart Job Board platform) exposes an RSS feed at
``/rss/`` but the feed returns only the 10 most-recent listings with no
keyword filtering support.  HTML search results are used instead.

Discovery (2026-06-22)
-----------------------
- Search URL: GET /jobs/?keywords[all_words]={keyword}&page=N
- 20 cards per page
- robots.txt: permissive, no Cloudflare/bot management
- No authentication required

Card structure (confirmed 2026-06-22)
--------------------------------------
  article.listing-item
    .media-heading.listing-item__title a.link  → title + URL
    .listing-item__info--item-company          → employer
    .listing-item__info--item-location         → location_raw
    .listing-item__employment-type             → contract_type_raw
    .listing-item__date                        → posted_at  (DD/MM/YYYY)
    .listing-item__desc                        → description_raw

URL format: /job/{id}/{slug}/  → source_listing_id = {id}

Fetch strategy
--------------
For each keyword in keywords_list, fetch pages 1..max_pages_per_query.
Stops when a page yields zero cards (exhausted results).
Deduplicates by source_listing_id across all keywords/pages.
Applies since filter client-side on posted_at.
Polite delay of _PAGE_DELAY_SECONDS between every page request.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.construction_jobboard")

_SOURCE_NAME = "construction_jobboard"
_DEFAULT_BASE_URL = "https://www.constructionjobboard.co.uk"
_SEARCH_PATH = "/jobs/"
_MAX_PAGES = 5
_PAGE_DELAY_SECONDS = 3
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

# Regex to extract the numeric job ID from a CJB job URL.
# URL form: /job/3677619/project-manager/
_RE_JOB_ID = re.compile(r"/job/(\d+)/")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_search_url(base_url: str, keyword: str, page: int = 1) -> str:
    """Build a CJB search URL for one keyword and page number.

    Example::

        >>> _build_search_url("https://www.constructionjobboard.co.uk", "project manager", 1)
        'https://www.constructionjobboard.co.uk/jobs/?keywords%5Ball_words%5D=project+manager&page=1'
    """
    encoded = quote_plus(keyword)
    return f"{base_url}{_SEARCH_PATH}?keywords%5Ball_words%5D={encoded}&page={page}"


def _extract_job_id(url: str) -> str:
    """Extract the numeric job ID from a CJB job URL.

    Falls back to the last path segment if no numeric ID is found.

    Examples::

        >>> _extract_job_id("https://www.constructionjobboard.co.uk/job/3677619/project-manager/")
        '3677619'
    """
    m = _RE_JOB_ID.search(url)
    if m:
        return m.group(1)
    # Fallback: last non-empty path segment
    parts = [p for p in url.rstrip("/").split("/") if p]
    return parts[-1] if parts else url


def _parse_date(raw: str | None) -> datetime | None:
    """Parse a CJB listing date string to a UTC-aware datetime.

    Confirmed format (2026-06-22): ``DD/MM/YYYY`` e.g. ``11/06/2026``.
    Also handles ISO formats for resilience.
    Returns None on any parse failure or empty input.
    """
    if not raw:
        return None
    raw = raw.strip()
    for fmt in (
        "%d/%m/%Y",           # confirmed CJB format
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    iso_m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso_m:
        try:
            return datetime.strptime(iso_m.group(1), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    logger.debug("construction_jobboard: could not parse date string: %r", raw)
    return None


def _card_to_raw_listing(card, base_url: str) -> RawListing | None:
    """Map one ``article.listing-item`` node to a RawListing.

    Returns None if the required title or URL cannot be extracted.

    Confirmed field map (2026-06-22):
      .media-heading.listing-item__title a.link   → title + URL
      .listing-item__info--item-company            → employer
      .listing-item__info--item-location           → location_raw
      .listing-item__employment-type               → contract_type_raw
      .listing-item__date                          → posted_at
      .listing-item__desc                          → description_raw
    """
    # --- Title + URL ---
    title_node = card.css_first(".listing-item__title a.link")
    if title_node is None:
        title_node = card.css_first("a.link")
    if title_node is None:
        return None

    title = title_node.text(strip=True)
    if not title:
        return None

    href = title_node.attributes.get("href", "")
    url = href if href.startswith("http") else urljoin(base_url, href)
    listing_id = _extract_job_id(url)

    # --- Employer ---
    emp_node = card.css_first(".listing-item__info--item-company")
    employer: str | None = emp_node.text(strip=True) if emp_node else None
    if not employer:
        employer = None

    # --- Location ---
    loc_node = card.css_first(".listing-item__info--item-location")
    location_raw: str | None = loc_node.text(strip=True) if loc_node else None
    if not location_raw:
        location_raw = None

    # --- Contract type ---
    ct_node = card.css_first(".listing-item__employment-type")
    contract_type_raw: str | None = ct_node.text(strip=True) if ct_node else None
    if not contract_type_raw:
        contract_type_raw = None

    # --- Date ---
    date_node = card.css_first(".listing-item__date")
    posted_at = _parse_date(date_node.text(strip=True) if date_node else None)

    # --- Description ---
    desc_node = card.css_first(".listing-item__desc")
    description_raw: str | None = desc_node.text(strip=True) if desc_node else None
    if not description_raw:
        description_raw = None

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=listing_id,
        url=url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=None,
        contract_type_raw=contract_type_raw,
        metadata={"page_url": url},
    )


def _parse_html(html: str, base_url: str, page_url: str) -> list[RawListing]:
    """Parse a CJB search-results HTML page into RawListings.

    Extracted as a standalone function so fixture-based unit tests can call it
    without making live HTTP requests.  Returns [] on any parse failure.
    """
    if not html or not html.strip():
        return []
    try:
        tree = HTMLParser(html)
    except Exception:
        logger.exception(
            "construction_jobboard: HTMLParser failed for %s", page_url
        )
        return []

    cards = tree.css("article.listing-item")
    if not cards:
        logger.debug(
            "construction_jobboard: no article.listing-item cards in %s.",
            page_url,
        )
        return []

    listings: list[RawListing] = []
    for card in cards:
        listing = _card_to_raw_listing(card, base_url)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "construction_jobboard: %s → %d listing(s) parsed.",
        page_url,
        len(listings),
    )
    return listings


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ConstructionJobBoardAdapter(SourceAdapter):
    """Adapter for ConstructionJobBoard.co.uk — Smart Job Board HTML scrape.

    RSS probe (2026-06-22)
    ~~~~~~~~~~~~~~~~~~~~~~
    The ``/rss/`` endpoint returns only the 10 most-recent listings with no
    keyword-filter support.  HTML search results at ``/jobs/`` are used
    instead.

    Search
    ~~~~~~
    GET /jobs/?keywords[all_words]={keyword}&page=N
    20 cards per page.  Pagination stops when a page returns zero cards.

    Multi-query
    ~~~~~~~~~~~
    Iterates ``keywords_list``; deduplicates within-source by
    ``source_listing_id`` before returning.

    Returns [] on any unrecoverable error; never crashes the pipeline.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        crawl_delay: int = 3,
        keywords_list: list[str] | None = None,
        max_pages_per_query: int = _MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.crawl_delay = crawl_delay
        self.keywords_list: list[str] = keywords_list or list(_DEFAULT_KEYWORDS)
        self.max_pages_per_query = max_pages_per_query

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Scrape ConstructionJobBoard.co.uk for job listings.

        Iterates ``self.keywords_list``; for each keyword paginates up to
        ``max_pages_per_query`` pages.  Stops early when a page yields zero
        cards.  Sleeps ``_PAGE_DELAY_SECONDS`` between every page request.
        Deduplicates within-source by ``source_listing_id``.  Applies
        ``since`` filter client-side on ``posted_at`` (listings without a
        date are always kept).  Returns [] and logs a warning on any failure.
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
                    for page in range(1, self.max_pages_per_query + 1):
                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False

                        url = _build_search_url(self.base_url, keyword, page)
                        page_listings = await self._fetch_page(client, url, page)

                        if not page_listings:
                            logger.debug(
                                "construction_jobboard: page %d empty for "
                                "keyword %r — stopping pagination.",
                                page,
                                keyword,
                            )
                            break

                        new_count = 0
                        for listing in page_listings:
                            if (
                                since
                                and listing.posted_at
                                and listing.posted_at < since
                            ):
                                continue
                            lid = listing.source_listing_id
                            if lid and lid not in seen:
                                seen[lid] = listing
                                new_count += 1

                        logger.debug(
                            "construction_jobboard: keyword=%r page=%d → "
                            "%d results, %d new unique.",
                            keyword,
                            page,
                            len(page_listings),
                            new_count,
                        )

        except Exception:
            logger.exception(
                "construction_jobboard: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "construction_jobboard: fetched %d unique listing(s) "
            "(keywords=%d, max_pages=%d, since=%s).",
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
        page_num: int,
    ) -> list[RawListing]:
        """Fetch one search-results page and return parsed listings.

        Returns [] on any error — never raises.
        """
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "construction_jobboard: HTTP %s on page %d (%s).",
                exc.response.status_code,
                page_num,
                url,
            )
            return []
        except Exception:
            logger.exception(
                "construction_jobboard: request failed on page %d (%s).",
                page_num,
                url,
            )
            return []

        return _parse_html(response.text, self.base_url, url)
