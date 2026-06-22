"""Kier Careers adapter — HTML scrape of jobs.kier.co.uk.

Overview
--------
Kier Group's careers portal (jobs.kier.co.uk) serves plain HTML search
results at ``/jobs/search?page=N&query={keyword}``.  No CAPTCHA or
bot-management blocking observed (2026-06-22).

Discovery (2026-06-22)
-----------------------
- Search URL: GET /jobs/search?page=N&query={keyword}
- 15 cards per page; paginated via ``page`` query param
- Detail pages: /jobs/{slug}-{optional-uuid}
- robots.txt: disallows /api/ and /v1/candidate_details; /jobs/search is
  explicitly allowed
- No authentication required

Card structure (confirmed 2026-06-22)
--------------------------------------
  article.col-12.job-search-results-card-col
    h3.card-title.job-search-results-card-title a  → title + URL
    li.job-component-location span                 → location_raw
    li.job-component-employment-type span          → contract_type_raw
    li.job-component-category span                 → category (metadata)
    p.card-text.job-search-results-summary         → description_raw
  Employer: "Kier Group" (fixed; direct employer site)
  Salary: not present in search cards
  Date: not present in search cards (posted_at = None)

Pagination stop condition
--------------------------
Stops when the fetched page returns zero cards, or when the
``li.next_page`` element in the pagination has the ``disabled`` CSS class.

ID extraction
-------------
The job URL slug may end with a UUID, e.g.:
  /jobs/commercial-manager-glasgow-strathclyde-united-kingdom-493d7be3-95d1-406c-99ab-5574d777dd43
UUID is used as the ID when present.  Otherwise the full path slug is used.

Fetch strategy
--------------
For each keyword in keywords_list, fetch pages 1..max_pages_per_query.
Stops early when a page yields zero cards or the next-page link is disabled.
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

logger = logging.getLogger("mechpm.adapter.kier")

_SOURCE_NAME = "kier"
_DEFAULT_BASE_URL = "https://jobs.kier.co.uk"
_SEARCH_PATH = "/jobs/search"
_EMPLOYER = "Kier Group"
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

# UUID pattern at the end of a Kier job URL slug.
_RE_UUID = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_search_url(base_url: str, keyword: str, page: int = 1) -> str:
    """Build a Kier Careers search URL for one keyword and page.

    Example::

        >>> _build_search_url("https://jobs.kier.co.uk", "project manager", 1)
        'https://jobs.kier.co.uk/jobs/search?page=1&query=project+manager'
    """
    return f"{base_url}{_SEARCH_PATH}?page={page}&query={quote_plus(keyword)}"


def _extract_listing_id(url: str) -> str:
    """Extract a stable identifier from a Kier job URL.

    Prefers a trailing UUID when present (most stable); falls back to the
    full URL path slug, which is unique enough for deduplication.

    Examples::

        >>> _extract_listing_id(
        ...     "https://jobs.kier.co.uk/jobs/commercial-manager-glasgow-"
        ...     "strathclyde-united-kingdom-493d7be3-95d1-406c-99ab-5574d777dd43"
        ... )
        '493d7be3-95d1-406c-99ab-5574d777dd43'
        >>> _extract_listing_id(
        ...     "https://jobs.kier.co.uk/jobs/project-manager-falmer-east-sussex-united-kingdom"
        ... )
        'project-manager-falmer-east-sussex-united-kingdom'
    """
    # Try to find a trailing UUID
    path = url.rstrip("/")
    # Last segment after the final slash
    last_segment = path.rsplit("/", 1)[-1] if "/" in path else path
    m = _RE_UUID.search(last_segment)
    if m:
        return m.group(1)
    return last_segment


def _is_last_page(tree: HTMLParser) -> bool:
    """Return True when the pagination indicates there is no next page.

    Checks whether the ``li.next_page`` element carries the ``disabled``
    CSS class, which Kier Careers adds on the final page.
    """
    next_li = tree.css_first("li.next_page")
    if next_li is None:
        return False
    cls = next_li.attributes.get("class", "")
    return "disabled" in cls


def _card_to_raw_listing(card, base_url: str) -> RawListing | None:
    """Map one ``article.col-12.job-search-results-card-col`` to a RawListing.

    Returns None if the required title or URL cannot be extracted.

    Confirmed field map (2026-06-22):
      h3.card-title a                          → title + URL
      li.job-component-location span           → location_raw
      li.job-component-employment-type span    → contract_type_raw
      li.job-component-category span           → category (metadata)
      p.card-text.job-search-results-summary   → description_raw
    """
    # --- Title + URL ---
    title_node = card.css_first("h3.card-title a")
    if title_node is None:
        title_node = card.css_first("h3 a")
    if title_node is None:
        return None

    title = title_node.text(strip=True)
    if not title:
        return None

    href = title_node.attributes.get("href", "")
    url = href if href.startswith("http") else urljoin(base_url, href)
    listing_id = _extract_listing_id(url)

    # --- Location ---
    loc_node = card.css_first("li.job-component-location span")
    if loc_node is None:
        loc_node = card.css_first(".job-component-location span")
    location_raw: str | None = loc_node.text(strip=True) if loc_node else None
    if not location_raw:
        location_raw = None

    # --- Employment / contract type ---
    ct_node = card.css_first("li.job-component-employment-type span")
    if ct_node is None:
        ct_node = card.css_first(".job-component-employment-type span")
    contract_type_raw: str | None = ct_node.text(strip=True) if ct_node else None
    if not contract_type_raw:
        contract_type_raw = None

    # --- Category (metadata) ---
    cat_node = card.css_first("li.job-component-category span")
    category: str | None = cat_node.text(strip=True) if cat_node else None

    # --- Description snippet ---
    desc_node = card.css_first("p.card-text")
    if desc_node is None:
        desc_node = card.css_first("p.job-search-results-summary")
    description_raw: str | None = desc_node.text(strip=True) if desc_node else None
    if not description_raw:
        description_raw = None

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=listing_id,
        url=url,
        title=title,
        employer=_EMPLOYER,
        agency=None,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=None,
        salary_raw=None,
        contract_type_raw=contract_type_raw,
        metadata={
            "category": category,
        },
    )


def _parse_html(html: str, base_url: str, page_url: str) -> tuple[list[RawListing], bool]:
    """Parse a Kier Careers search-results HTML page into RawListings.

    Extracted as a standalone function so fixture-based unit tests can call it
    without making live HTTP requests.

    Returns a tuple of ``(listings, is_last_page)``.
    Returns ``([], False)`` on any parse failure — never raises.
    """
    if not html or not html.strip():
        return [], False
    try:
        tree = HTMLParser(html)
    except Exception:
        logger.exception("kier: HTMLParser failed for %s", page_url)
        return [], False

    cards = tree.css("article.col-12.job-search-results-card-col")
    if not cards:
        # Try a less-specific selector as fallback
        cards = tree.css("article.job-search-results-card-col")

    last_page = _is_last_page(tree)

    listings: list[RawListing] = []
    for card in cards:
        listing = _card_to_raw_listing(card, base_url)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "kier: %s → %d listing(s) parsed (last_page=%s).",
        page_url,
        len(listings),
        last_page,
    )
    return listings, last_page


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class KierAdapter(SourceAdapter):
    """Adapter for Kier Careers — HTML scrape of jobs.kier.co.uk.

    Search URL
    ~~~~~~~~~~
    ``/jobs/search?page=N&query={keyword}``

    The employer for all listings is fixed as ``"Kier Group"`` since this
    is Kier's own direct careers site.  No salary or posted-date is
    available in the search-results cards.

    Pagination
    ~~~~~~~~~~
    Stops when a page yields zero cards, the ``li.next_page.disabled``
    element is present in the pagination, or ``max_pages_per_query`` is
    reached.

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
        """Scrape Kier Careers for job listings.

        Iterates ``self.keywords_list``; for each keyword paginates up to
        ``max_pages_per_query`` pages.  Stops early when a page yields zero
        cards or the pagination signals the last page.  Sleeps
        ``_PAGE_DELAY_SECONDS`` between every page request.  Deduplicates
        within-source by ``source_listing_id``.  Applies ``since`` filter
        client-side on ``posted_at``; since Kier cards lack a post date,
        the filter only has effect if a date is somehow populated in future.
        Returns [] and logs a warning on any failure.
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
                        page_listings, is_last = await self._fetch_page(
                            client, url, page
                        )

                        if not page_listings:
                            logger.debug(
                                "kier: page %d empty for keyword %r — "
                                "stopping pagination.",
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
                            "kier: keyword=%r page=%d → %d results, "
                            "%d new unique (last=%s).",
                            keyword,
                            page,
                            len(page_listings),
                            new_count,
                            is_last,
                        )

                        if is_last:
                            break

        except Exception:
            logger.exception(
                "kier: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "kier: fetched %d unique listing(s) "
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
    ) -> tuple[list[RawListing], bool]:
        """Fetch one search-results page and return (listings, is_last_page).

        Retries up to 3 times on transient failures (202 Accepted, network
        errors).  Returns ([], False) on any unrecoverable error.
        """
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "kier: HTTP %s on page %d (%s), attempt %d/%d.",
                    exc.response.status_code,
                    page_num,
                    url,
                    attempt + 1,
                    max_attempts,
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                return [], False
            except Exception:
                logger.exception(
                    "kier: request failed on page %d (%s), attempt %d/%d.",
                    page_num,
                    url,
                    attempt + 1,
                    max_attempts,
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                return [], False

            # 202 Accepted means the server is still processing — retry
            if response.status_code == 202:
                logger.warning(
                    "kier: HTTP 202 Accepted on page %d (attempt %d/%d) — retrying.",
                    page_num,
                    attempt + 1,
                    max_attempts,
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                return [], False

            return _parse_html(response.text, self.base_url, url)

        return [], False
