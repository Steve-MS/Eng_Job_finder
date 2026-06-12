"""Energy Jobline adapter — Jobiqo/Drupal platform HTML scrape.

Search URL: https://www.energyjobline.com/jobs
            ?keywords=project+manager+mechanical
            &location=United+Kingdom
            &contract_type=contract

robots.txt:  Crawl-delay: 10. /search/ disallowed (legacy); /jobs?... allowed.
Pagination:  &page=N  (1-based; capped at _MAX_PAGES for MVP).
Parsing:     selectolax — tries multiple Drupal/Jobiqo card selectors in order,
             falls back gracefully when none match.
Detail fetch: skipped for MVP; metadata["detail_fetched"] = False.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.energy_jobline")

_BASE_URL = "https://www.energyjobline.com"
_SEARCH_URL = (
    "https://www.energyjobline.com/jobs"
    "?keywords=project+manager+mechanical"
    "&location=United+Kingdom"
    "&contract_type=contract"
)
_MAX_PAGES = 5
_PAGE_DELAY_SECONDS = 10  # robots.txt Crawl-delay: 10
_REQUEST_TIMEOUT = 30.0

# Ordered by likelihood on Jobiqo/Drupal themes — first non-empty match wins.
_CARD_SELECTORS = [
    "article.job-result",
    "article.job-item",
    "li.job-result",
    "li.job-item",
    "[itemtype*='JobPosting']",
    "div.job-listing",
    "div.views-row",
    "[data-job-id]",
]

_TITLE_SELECTORS = [
    "[itemprop='title']",
    "h2.job-title a",
    "h3.job-title a",
    ".job-title a",
    ".title a",
    "h2 a",
    "h3 a",
]
_EMPLOYER_SELECTORS = [
    "[itemprop='hiringOrganization'] [itemprop='name']",
    "[itemprop='hiringOrganization']",
    ".employer-name",
    ".company-name",
    ".employer",
    ".company",
    "[data-employer]",
]
_LOCATION_SELECTORS = [
    "[itemprop='jobLocation'] [itemprop='name']",
    "[itemprop='jobLocation']",
    ".job-location",
    ".location",
    "[data-location]",
]
_SALARY_SELECTORS = [
    "[itemprop='baseSalary']",
    ".salary",
    ".pay-rate",
    ".rate",
    ".field-job-salary",
    ".field-salary",
]
_DATE_SELECTORS = [
    "[itemprop='datePosted']",
    "time.date",
    ".date",
    ".posted-date",
    ".created",
    ".field-posted-date",
    "time",
]
_SNIPPET_SELECTORS = [
    "[itemprop='description']",
    ".job-snippet",
    ".description",
    ".summary",
    ".views-field-body",
    ".field-body",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_text(node, selectors: list[str]) -> str | None:
    """Try each CSS selector; return stripped text of first match."""
    for sel in selectors:
        match = node.css_first(sel)
        if match:
            text = match.text(strip=True)
            if text:
                return text
    return None


def _extract_listing_id(url: str) -> str:
    """Extract a stable ID from a listing URL.

    Energy Jobline URLs are typically ``/job/{numeric-id}/{slug}`` or
    ``/jobs/{numeric-id}/{slug}``.  Falls back to the last path segment.
    """
    path = urlparse(url).path
    segments = [s for s in path.strip("/").split("/") if s]
    for seg in segments:
        if re.match(r"^\d+$", seg):
            return seg
    return segments[-1] if segments else url


def _parse_date(raw: str | None) -> datetime | None:
    """Parse date strings commonly emitted by Jobiqo/Drupal themes."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Tolerate ISO timestamps with offset junk: grab the date prefix only.
    iso_match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso_match:
        try:
            return datetime.strptime(iso_match.group(1), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    logger.debug("energy_jobline: could not parse date string: %r", raw)
    return None


def _find_cards(tree: HTMLParser) -> list:
    """Try card selectors in order; return the first non-empty result list."""
    for sel in _CARD_SELECTORS:
        cards = tree.css(sel)
        if cards:
            logger.debug(
                "energy_jobline: card selector %r matched %d card(s).", sel, len(cards)
            )
            return cards
    return []


def _card_to_raw_listing(card, page_url: str) -> RawListing | None:
    """Map one parsed card node to a RawListing.  Returns None if title absent."""
    # --- Title + listing URL ---
    title: str | None = None
    listing_url: str = ""
    for sel in _TITLE_SELECTORS:
        match = card.css_first(sel)
        if match:
            title = match.text(strip=True)
            href = match.attributes.get("href", "")
            if href:
                listing_url = urljoin(_BASE_URL, href)
            break

    if not title:
        return None

    # --- Listing ID ---
    listing_id: str = (
        card.attributes.get("data-job-id")
        or card.attributes.get("data-id")
        or (listing_url and _extract_listing_id(listing_url))
        or ""
    )

    # --- Employer ---
    employer = _get_text(card, _EMPLOYER_SELECTORS)

    # --- Location ---
    location_raw = _get_text(card, _LOCATION_SELECTORS)

    # --- Salary ---
    salary_raw = _get_text(card, _SALARY_SELECTORS)

    # --- Posted date: prefer datetime/content attribute on <time> elements ---
    posted_at: datetime | None = None
    for sel in _DATE_SELECTORS:
        match = card.css_first(sel)
        if match:
            dt_attr = match.attributes.get("datetime") or match.attributes.get(
                "content"
            )
            posted_at = _parse_date(dt_attr) or _parse_date(match.text(strip=True))
            if posted_at:
                break

    # --- Snippet ---
    snippet = _get_text(card, _SNIPPET_SELECTORS)

    return RawListing(
        source="energy_jobline",
        source_listing_id=str(listing_id),
        url=listing_url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=snippet,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw="contract",
        metadata={
            "detail_fetched": False,
            "page_url": page_url,
        },
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class EnergyJoblineAdapter(SourceAdapter):
    """Adapter for energyjobline.com — Jobiqo/Drupal HTML scrape.

    Fetches up to ``_MAX_PAGES`` pages of contract PM listings.
    Respects robots.txt ``Crawl-delay: 10`` between page requests.
    Returns ``[]`` on any unrecoverable error; never crashes the pipeline.
    """

    name = "energy_jobline"

    def __init__(self, crawl_delay: int = 10, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Scrape energyjobline.com for contract PM listings.

        Paginates up to _MAX_PAGES; sleeps _PAGE_DELAY_SECONDS between pages.
        Applies ``since`` filter client-side on ``posted_at`` (best-effort —
        many cards omit the posted date).
        Returns [] and logs a warning on any failure.
        """
        listings: list[RawListing] = []
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for page_num in range(1, _MAX_PAGES + 1):
                    url = (
                        _SEARCH_URL
                        if page_num == 1
                        else f"{_SEARCH_URL}&page={page_num}"
                    )
                    page_listings = await self._fetch_page(client, url, page_num)

                    if not page_listings:
                        if page_num == 1:
                            logger.warning(
                                "energy_jobline: page 1 returned no listings — "
                                "possible selector mismatch or site change. "
                                "Returning []."
                            )
                        else:
                            logger.debug(
                                "energy_jobline: page %d empty — stopping pagination.",
                                page_num,
                            )
                        break

                    for listing in page_listings:
                        if since and listing.posted_at and listing.posted_at < since:
                            continue
                        listings.append(listing)

                    if page_num < _MAX_PAGES:
                        await asyncio.sleep(_PAGE_DELAY_SECONDS)

        except Exception:
            logger.exception(
                "energy_jobline: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(listings),
            )
            return listings

        logger.info(
            "energy_jobline: fetched %d listing(s) (max_pages=%d, since=%s).",
            len(listings),
            _MAX_PAGES,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        page_num: int,
    ) -> list[RawListing]:
        """Fetch and parse one search-results page.  Returns [] on any error."""
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "energy_jobline: HTTP %s on page %d (%s).",
                exc.response.status_code,
                page_num,
                url,
            )
            return []
        except Exception:
            logger.exception(
                "energy_jobline: request failed on page %d (%s).", page_num, url
            )
            return []

        try:
            tree = HTMLParser(response.text)
        except Exception:
            logger.exception(
                "energy_jobline: HTML parse error on page %d.", page_num
            )
            return []

        cards = _find_cards(tree)
        if not cards:
            logger.warning(
                "energy_jobline: no job cards found on page %d — "
                "tried selectors: [%s]. DOM may have changed.",
                page_num,
                ", ".join(_CARD_SELECTORS),
            )
            return []

        page_listings: list[RawListing] = []
        for card in cards:
            listing = _card_to_raw_listing(card, url)
            if listing is not None:
                page_listings.append(listing)

        logger.debug(
            "energy_jobline: page %d → %d listing(s) parsed from %d card(s).",
            page_num,
            len(page_listings),
            len(cards),
        )
        return page_listings


# ---------------------------------------------------------------------------
# Self-test (print only here)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    adapter = EnergyJoblineAdapter()
    results = asyncio.run(adapter.fetch())
    print(f"Fetched {len(results)} listing(s) from Energy Jobline.")
    if results:
        first = results[0]
        print(
            f"  [0] title={first.title!r} | employer={first.employer!r} | "
            f"location={first.location_raw!r} | salary={first.salary_raw!r}"
        )
        print(f"      url={first.url}")
        print(f"      id={first.source_listing_id!r} | posted_at={first.posted_at}")
    else:
        print(
            "  No listings returned — check selectors or network connection.",
            file=sys.stderr,
        )
