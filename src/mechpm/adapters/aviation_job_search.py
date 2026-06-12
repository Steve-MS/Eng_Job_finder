"""Aviation Job Search adapter.

Strategy:   HTML GET with realistic browser headers → selectolax CSS-selector parsing.
URL:        https://www.aviationjobsearch.com/en-GB/jobs
            ?title=project+manager&job_categories=Engineering&contract_types=2
robots.txt: Disallows /api/* and action paths only; Allow: / for all public pages.
Crawl-delay: 3 s between page requests (per robots.txt and binding team decision).
Pagination: &page=N (1-indexed), capped at page 5.
Aggregator: This board aggregates employer ATS feeds → metadata["aggregator"] = True
            so Ada's dedup pass knows to weight source_listing_id over employer match.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser, Node

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.aviation_job_search")

_BASE_URL = "https://www.aviationjobsearch.com"
_SEARCH_PATH = "/en-GB/jobs"
_SEARCH_PARAMS: dict[str, str] = {
    "title": "project manager",
    "job_categories": "Engineering",
    "contract_types": "2",
}
_MAX_PAGES = 5
_PAGE_DELAY_SECONDS = 3
_REQUEST_TIMEOUT = 30.0

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
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Ordered candidate selectors for job listing cards.
# The adapter tries each in sequence and uses the first that yields results.
_CARD_SELECTORS = [
    "[data-test='job-item']",
    "[data-testid='job-card']",
    "[data-testid='job-item']",
    "article.job",
    "li.job-listing",
    "div.job-listing",
    "div.job-result-card",
    "div.job-card",
    ".listing-item",
    ".search-result-item",
    "li[class*='job']",
    "div[class*='job-item']",
    "div[class*='job-card']",
]

# Sub-selector candidates for each field (first match wins).
_TITLE_SELECTORS = [
    "[data-test='job-title']",
    "[data-testid='job-title']",
    "h2 a",
    "h3 a",
    "h2",
    "h3",
    ".job-title a",
    ".job-title",
    "a.title",
    ".title a",
    ".title",
]
_EMPLOYER_SELECTORS = [
    "[data-test='employer']",
    "[data-testid='employer']",
    ".company-name",
    ".employer-name",
    ".employer",
    ".company",
    "[class*='employer']",
    "[class*='company']",
]
_LOCATION_SELECTORS = [
    "[data-test='location']",
    "[data-testid='location']",
    ".location",
    ".job-location",
    "[class*='location']",
    "span[itemprop='addressLocality']",
]
_SALARY_SELECTORS = [
    "[data-test='salary']",
    "[data-testid='salary']",
    ".salary",
    ".rate",
    ".compensation",
    "[class*='salary']",
    "[class*='rate']",
]
_DATE_SELECTORS = [
    "time",
    "[data-test='posted-date']",
    "[data-testid='posted-date']",
    ".posted-date",
    ".listing-date",
    "[class*='posted']",
    "[class*='date']",
    ".date",
]


def _parse_date_str(text: str | None) -> datetime | None:
    """Parse Aviation Job Search date strings (relative or absolute) to UTC datetime."""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    now = datetime.now(timezone.utc)
    lower = text.lower()

    # Relative formats
    if lower in ("today", "just now", "less than a day ago"):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if lower == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.match(r"(\d+)\s*(day|days|week|weeks|month|months)\s+ago", lower)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if "day" in unit:
            return now - timedelta(days=n)
        if "week" in unit:
            return now - timedelta(weeks=n)
        if "month" in unit:
            return now - timedelta(days=n * 30)

    # Absolute formats
    for fmt in (
        "%d %b %Y",
        "%d %B %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    logger.debug("Aviation Job Search: could not parse date string %r", text)
    return None


def _extract_listing_id(url: str) -> str:
    """Return a stable listing ID from the URL path.

    Prefers a numeric path segment (the canonical job-ID on most boards).
    Falls back to the last non-empty path segment.
    """
    if not url:
        return ""
    path = urlparse(url).path
    segments = [s for s in path.strip("/").split("/") if s]
    for seg in segments:
        if re.match(r"^\d+$", seg):
            return seg
    return segments[-1] if segments else url


def _node_text(node: Node, *selectors: str) -> str | None:
    """Return stripped text from the first matching sub-selector inside *node*."""
    for sel in selectors:
        try:
            el = node.css_first(sel)
            if el:
                text = el.text(strip=True)
                if text:
                    return text
        except Exception:  # pragma: no cover — defensive
            continue
    return None


def _link_href(node: Node, selector: str = "a[href]") -> str | None:
    """Return the href attribute from the first anchor matching *selector*."""
    try:
        el = node.css_first(selector)
        if el:
            return el.attributes.get("href")
    except Exception:  # pragma: no cover
        pass
    return None


class AviationJobSearchAdapter(SourceAdapter):
    """Adapter for aviationjobsearch.com.

    Strategy: HTML GET → selectolax parse.
    robots.txt: Disallows /api/* and action paths only; Allow: / for all public pages.
    Crawl-delay: 3 s between successive page requests.
    Aggregator flag: True — this board aggregates many employer ATS feeds,
        so Ada must treat source_listing_id as the primary dedup key.
    """

    name = "aviation_job_search"
    crawl_delay = 3

    def __init__(self, crawl_delay: int = 3, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch contract PM listings from Aviation Job Search (up to 5 pages).

        Sleeps _PAGE_DELAY_SECONDS between page requests.
        Applies ``since`` client-side on posted_at.
        Returns [] on any failure; never raises.
        """
        listings: list[RawListing] = []
        try:
            async with httpx.AsyncClient(
                headers=_BROWSER_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for page in range(1, _MAX_PAGES + 1):
                    params = {**_SEARCH_PARAMS, "page": str(page)}
                    page_listings = await self._fetch_page(client, page, params)

                    if not page_listings:
                        logger.info(
                            "Aviation Job Search: no listings on page %d — stopping early.",
                            page,
                        )
                        break

                    for listing in page_listings:
                        if since and listing.posted_at and listing.posted_at < since:
                            continue
                        listings.append(listing)

                    if page < _MAX_PAGES:
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

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        page: int,
        params: dict[str, str],
    ) -> list[RawListing]:
        """Fetch one search results page; returns [] on any HTTP or network error."""
        url = f"{_BASE_URL}{_SEARCH_PATH}"
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Aviation Job Search HTTP error (page=%d, status=%s): %s",
                page,
                exc.response.status_code,
                exc,
            )
            return []
        except Exception:
            logger.exception("Aviation Job Search request failed (page=%d).", page)
            return []

        return self._parse_page(response.text, page)

    def _parse_page(self, html: str, page: int) -> list[RawListing]:
        """Parse listing cards from raw HTML; returns [] if no selectors match."""
        tree = HTMLParser(html)
        cards = None
        matched_selector: str | None = None

        for sel in _CARD_SELECTORS:
            try:
                found = tree.css(sel)
                if found:
                    cards = found
                    matched_selector = sel
                    break
            except Exception:
                continue

        if not cards:
            logger.warning(
                "Aviation Job Search: no listing cards found on page %d. "
                "All selectors exhausted — HTML structure may have changed. "
                "Selectors tried: %s",
                page,
                _CARD_SELECTORS,
            )
            return []

        logger.debug(
            "Aviation Job Search: page %d — %d card(s) matched via selector %r.",
            page,
            len(cards),
            matched_selector,
        )

        listings: list[RawListing] = []
        for card in cards:
            try:
                listing = self._map_card(card, page)
                if listing is not None:
                    listings.append(listing)
            except Exception:
                logger.debug(
                    "Aviation Job Search: failed to map card (page=%d) — skipping.",
                    page,
                    exc_info=True,
                )
        return listings

    def _map_card(self, card: Node, page: int) -> RawListing | None:
        """Map a single card node to a RawListing; returns None if essential fields absent."""
        # Resolve listing URL
        href = _link_href(card)
        if not href:
            return None
        if not href.startswith("http"):
            href = urljoin(_BASE_URL, href)

        listing_id = _extract_listing_id(href)
        if not listing_id:
            return None

        title = _node_text(card, *_TITLE_SELECTORS)
        if not title:
            return None

        employer = _node_text(card, *_EMPLOYER_SELECTORS)
        location = _node_text(card, *_LOCATION_SELECTORS)
        salary = _node_text(card, *_SALARY_SELECTORS)

        # Date: try sub-selectors first, then <time datetime="..."> attribute
        date_text = _node_text(card, *_DATE_SELECTORS)
        if not date_text:
            try:
                time_el = card.css_first("time")
                if time_el:
                    date_text = (
                        time_el.attributes.get("datetime") or time_el.text(strip=True)
                    )
            except Exception:
                pass

        return RawListing(
            source="aviation_job_search",
            source_listing_id=listing_id,
            url=href,
            title=title,
            employer=employer,
            agency=None,
            location_raw=location,
            description_raw=None,
            posted_at=_parse_date_str(date_text),
            salary_raw=salary,
            contract_type_raw="contract",
            metadata={
                "aggregator": True,
            },
        )


if __name__ == "__main__":
    import asyncio as _asyncio
    import logging as _logging

    _logging.basicConfig(
        level=_logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    _adapter = AviationJobSearchAdapter()
    _results = _asyncio.run(_adapter.fetch())
    print(f"Fetched {len(_results)} listing(s) from Aviation Job Search.")
    for _r in _results[:5]:
        print(
            f"  [{_r.source_listing_id}] {_r.title!r}"
            f" | {_r.employer} | {_r.location_raw}"
            f" | {_r.posted_at} | {_r.salary_raw}"
        )
