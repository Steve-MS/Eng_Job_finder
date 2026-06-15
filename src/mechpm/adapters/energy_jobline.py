"""Energy Jobline adapter — Drupal/Jobiqo platform HTML scrape.

Confirmed structure (live recon 2026-06-15):
  - Plain HTML (no __NEXT_DATA__); Drupal Views listing page.
  - Card:     <article id="node-{nid}" class="... node--job-per-template ...">
  - Title:    h2.node__title a  (text; href is already absolute)
  - Employer: span.recruiter-company-profile-job-organization a
  - Location: div.location span  (optional — absent on some listings)
  - Date:     span.date  → text "MM/DD/YYYY,"  (US format, strip trailing comma)
  - Salary:   not present in search-result cards
  - ID:       regex node-(\\d+) on article id attribute

Search URL: https://www.energyjobline.com/jobs
            ?keywords=project+manager+mechanical
            &location=United+Kingdom
            &contract_type=contract

robots.txt:  Crawl-delay: 10. /search/ disallowed (legacy); /jobs?... allowed.
Pagination:  0-based &page=N  (page 1 = no param; page 2 = &page=1 etc.)
             Capped at _MAX_PAGES for the MVP.
Detail fetch: skipped for MVP; metadata["detail_fetched"] = False.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.energy_jobline")

_BASE_URL = "https://www.energyjobline.com"
# Legacy single-query URL (kept for backwards compatibility / tests).
_SEARCH_URL = (
    "https://www.energyjobline.com/jobs"
    "?keywords=project+manager+mechanical"
    "&location=United+Kingdom"
    "&contract_type=contract"
)
_MAX_PAGES = 5
_PAGE_DELAY_SECONDS = 10  # robots.txt Crawl-delay: 10
_REQUEST_TIMEOUT = 30.0


def _build_ejl_search_urls(
    keywords_list: list[str],
    location: str = "United Kingdom",
    contract_type: str = "contract",
) -> list[str]:
    """Build Energy Jobline search URLs from a keywords list.

    Uses ``quote_plus`` so spaces become ``+`` (e.g. ``"project manager"``
    → ``"project+manager"``), matching EJL's expected URL format.
    """
    return [
        (
            "https://www.energyjobline.com/jobs"
            f"?keywords={quote_plus(kw)}"
            f"&location={quote_plus(location)}"
            f"&contract_type={quote_plus(contract_type)}"
        )
        for kw in keywords_list
    ]

# Confirmed primary selector from live recon 2026-06-15.
# Fallbacks cover potential future theme changes.
_CARD_SELECTORS = [
    "article.node--job-per-template",   # confirmed 2026-06-15
    "article.node-job",                  # Drupal node class alternative
    "article[id^='node-']",              # id-attribute fallback
    "[itemtype*='JobPosting']",          # schema.org fallback
    "article.job-result",               # legacy Jobiqo theme
    "div.views-row",                    # generic Drupal Views row
]

# Confirmed from live recon; h2.node__title a is the working selector.
_TITLE_SELECTORS = [
    "h2.node__title a",
    ".node__title a",
    "h2 a.recruiter-job-link",
    "[itemprop='title']",
    "h2 a",
    "h3 a",
]
# Confirmed: employer is in span.recruiter-company-profile-job-organization > a
_EMPLOYER_SELECTORS = [
    ".recruiter-company-profile-job-organization a",
    ".recruiter-company-profile-job-organization",
    "[itemprop='hiringOrganization'] [itemprop='name']",
    "[itemprop='hiringOrganization']",
    ".employer-name",
    ".company-name",
    ".employer",
    ".company",
]
# Confirmed: location in div.location > span (optional field)
_LOCATION_SELECTORS = [
    ".location span",
    ".location",
    "[itemprop='jobLocation'] [itemprop='name']",
    "[itemprop='jobLocation']",
    ".job-location",
]
_SALARY_SELECTORS = [
    "[itemprop='baseSalary']",
    ".salary",
    ".pay-rate",
    ".rate",
    ".field-job-salary",
    ".field-salary",
]
# Confirmed: date in span.date with text "MM/DD/YYYY," (strip trailing comma)
_DATE_SELECTORS = [
    ".description .date",
    ".date",
    "[itemprop='datePosted']",
    "time.date",
    ".posted-date",
    ".created",
    "time",
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


def _extract_listing_id_from_url(url: str) -> str:
    """Fallback: extract numeric ID from listing URL slug (e.g. /job/slug-28782419)."""
    path = urlparse(url).path
    segments = [s for s in path.strip("/").split("/") if s]
    for seg in segments:
        if re.match(r"^\d+$", seg):
            return seg
    # Last segment may be "slug-nid" — try splitting on "-" and taking last part
    if segments:
        last = segments[-1]
        parts = last.split("-")
        if parts and re.match(r"^\d+$", parts[-1]):
            return parts[-1]
    return segments[-1] if segments else url


def _parse_date(raw: str | None) -> datetime | None:
    """Parse date strings emitted by the EJL Drupal theme.

    Confirmed format on 2026-06-15: ``MM/DD/YYYY`` (US format) with an
    optional trailing comma.  ISO 8601 variants are also handled for
    resilience.
    """
    if not raw:
        return None
    raw = raw.strip().rstrip(",").strip()
    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",           # confirmed EJL format: 06/15/2026
        "%d/%m/%Y",           # legacy fallback
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
    # Tolerate ISO timestamps with arbitrary offset: grab the date prefix only.
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
    """Map one parsed card node to a RawListing.  Returns None if title absent.

    Confirmed field map (live recon 2026-06-15):
      article id attr  → source_listing_id  (regex node-(\\d+))
      h2.node__title a → title + url        (href is absolute)
      .recruiter-company-profile-job-organization a → employer
      .location span   → location_raw       (optional)
      .date text       → posted_at          (MM/DD/YYYY, strip comma)
    """
    # --- Listing ID from article id attribute (e.g. "node-28782419") ---
    card_id_attr = card.attributes.get("id", "")
    id_match = re.match(r"node-(\d+)", card_id_attr)
    listing_id: str = id_match.group(1) if id_match else ""

    # --- Title + URL from h2.node__title a ---
    title_node = card.css_first("h2.node__title a")
    if title_node is None:
        # Fallback: try broader selectors
        for sel in _TITLE_SELECTORS[1:]:
            title_node = card.css_first(sel)
            if title_node:
                break

    if title_node is None:
        return None

    title = title_node.text(strip=True)
    if not title:
        return None

    href = title_node.attributes.get("href", "")
    # Href is already absolute on EJL; urljoin handles both cases safely.
    listing_url = href if href.startswith("http") else urljoin(_BASE_URL, href)

    # If id attr extraction failed, fall back to URL-based extraction.
    if not listing_id and listing_url:
        listing_id = _extract_listing_id_from_url(listing_url)

    # --- Employer ---
    emp_node = card.css_first(".recruiter-company-profile-job-organization a")
    if emp_node is None:
        emp_node = card.css_first(".recruiter-company-profile-job-organization")
    employer: str | None = emp_node.text(strip=True) if emp_node else None
    if not employer:
        employer = _get_text(card, _EMPLOYER_SELECTORS[2:])

    # --- Location (optional on EJL search cards) ---
    loc_node = card.css_first(".location span")
    if loc_node is None:
        loc_node = card.css_first(".location")
    location_raw: str | None = loc_node.text(strip=True) if loc_node else None

    # --- Posted date ---
    posted_at: datetime | None = None
    for sel in _DATE_SELECTORS:
        date_node = card.css_first(sel)
        if date_node:
            # Prefer datetime/content attributes on <time> elements if present.
            dt_attr = (
                date_node.attributes.get("datetime")
                or date_node.attributes.get("content")
            )
            posted_at = _parse_date(dt_attr) or _parse_date(
                date_node.text(strip=True)
            )
            if posted_at:
                break

    # --- Salary (not in EJL search result cards; kept for future resilience) ---
    salary_raw = _get_text(card, _SALARY_SELECTORS)

    return RawListing(
        source="energy_jobline",
        source_listing_id=listing_id,
        url=listing_url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=None,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw="contract",
        metadata={
            "detail_fetched": False,
            "page_url": page_url,
        },
    )


def _parse_html(html: str, page_url: str) -> list[RawListing]:
    """Parse a raw HTML search-results page and return RawListings.

    Extracted as a standalone function so fixture-based unit tests can call it
    directly without making HTTP requests.
    """
    try:
        tree = HTMLParser(html)
    except Exception:
        logger.exception("energy_jobline: HTMLParser failed for %s", page_url)
        return []

    cards = _find_cards(tree)
    if not cards:
        logger.warning(
            "energy_jobline: no job cards found in HTML (%s) — "
            "tried selectors: [%s]. DOM may have changed.",
            page_url,
            ", ".join(_CARD_SELECTORS),
        )
        return []

    listings: list[RawListing] = []
    for card in cards:
        listing = _card_to_raw_listing(card, page_url)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "energy_jobline: %s → %d listing(s) parsed from %d card(s).",
        page_url,
        len(listings),
        len(cards),
    )
    return listings


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class EnergyJoblineAdapter(SourceAdapter):
    """Adapter for energyjobline.com — Drupal HTML scrape.

    Supports multi-query mode via ``keywords_list``: the adapter iterates
    each keyword, builds a search URL, paginates up to
    ``max_pages_per_query`` pages, and unions all results by
    ``source_listing_id`` (within-source dedup) before returning.

    Respects robots.txt ``Crawl-delay: 10`` between every page request.
    Returns ``[]`` on any unrecoverable error; never crashes the pipeline.

    Pagination is 0-based (EJL Drupal convention):
      page 1 → no &page param
      page 2 → &page=1
      page 3 → &page=2  … etc.
    """

    name = "energy_jobline"

    def __init__(
        self,
        crawl_delay: int = 10,
        keywords_list: list[str] | None = None,
        location: str = "United Kingdom",
        contract_type: str = "contract",
        max_pages_per_query: int = _MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.crawl_delay = crawl_delay
        self.max_pages_per_query = max_pages_per_query
        # Build search URLs from keywords_list; fall back to legacy single URL.
        if keywords_list:
            self.search_urls = _build_ejl_search_urls(
                keywords_list, location, contract_type
            )
        else:
            self.search_urls = [_SEARCH_URL]

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Scrape energyjobline.com for contract PM listings.

        Iterates ``self.search_urls``; for each URL paginates up to
        ``max_pages_per_query`` pages.  Sleeps ``_PAGE_DELAY_SECONDS``
        between every page request (robots.txt Crawl-delay: 10).
        Deduplicates within-source by ``source_listing_id`` before return.
        Applies ``since`` filter client-side on ``posted_at`` (best-effort —
        cards without a posted date are always included).
        Returns [] and logs a warning on any failure.
        """
        seen: dict[str, RawListing] = {}  # source_listing_id → listing
        first_request = True
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for search_url in self.search_urls:
                    for page_num in range(1, self.max_pages_per_query + 1):
                        url = (
                            search_url
                            if page_num == 1
                            else f"{search_url}&page={page_num - 1}"
                        )
                        # Respect Crawl-delay: 10 between every request.
                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False

                        page_listings = await self._fetch_page(client, url, page_num)

                        if not page_listings:
                            if page_num == 1:
                                logger.warning(
                                    "energy_jobline: page 1 returned no listings "
                                    "for %s — possible selector mismatch.",
                                    url,
                                )
                            else:
                                logger.debug(
                                    "energy_jobline: page %d empty for %s — "
                                    "stopping pagination.",
                                    page_num,
                                    search_url,
                                )
                            break

                        new_count = 0
                        for listing in page_listings:
                            if since and listing.posted_at and listing.posted_at < since:
                                continue
                            lid = listing.source_listing_id
                            if lid and lid not in seen:
                                seen[lid] = listing
                                new_count += 1

                        logger.debug(
                            "energy_jobline: %s page %d → %d results, %d new unique.",
                            search_url,
                            page_num,
                            len(page_listings),
                            new_count,
                        )

        except Exception:
            logger.exception(
                "energy_jobline: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "energy_jobline: fetched %d unique listing(s) "
            "(queries=%d, max_pages_per_query=%d, since=%s).",
            len(listings),
            len(self.search_urls),
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

        return _parse_html(response.text, url)


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
