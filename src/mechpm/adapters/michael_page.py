"""Michael Page adapter — Drupal HTML scrape.

Confirmed DOM structure (live recon 2026-06-17):
  Card:     <li class="views-row"><div class="job-tile search-job-tile">
  ID:       div.job-title  id attr  (numeric node ID, e.g. "9282906")
  Title:    div.job-title h3 a  (text; href is site-relative)
  Location: div.job-location  (text after <i> icon)
  Contract: div.job-contract-type  (text after <i>; "Interim", "Temporary", "Permanent")
  Salary:   div.job-salary  (text after <i>; optional; "£320 - £375 per day" or annual)
  Summary:  div.job_advert__job-summary-text + div.job_advert__job-desc-bullet-points
  Pager:    ul.pager__items a[rel=next] href → "?page=N"

Search URL: https://www.michaelpage.co.uk/jobs/{keyword-slug}
  keyword-slug = keyword string with spaces replaced by hyphens, lowercased.
  Only Drupal-configured paths return results; unmapped slugs return 404 (logged, skipped).

Confirmed working slugs (2026-06-17):
  project-manager, project-engineer, project-director, contracts-manager,
  programme-manager, engineering-project-manager

Pagination: ?page=N (0-indexed). Page 0 = no param; subsequent pages add ?page=N.
  Capped at _MAX_PAGES_DEFAULT.

robots.txt: /jobs/* allowed; only /jobs/*/*/*/ (4+ segments) disallowed.
  All search and listing pages at ≤3 segments are robots-compliant.

Crawl-delay: robots.txt does not specify a Crawl-delay for this domain.
  We use 5 s between requests out of courtesy (configurable).

All listings are returned regardless of contract type — the pipeline's
passes_contract() filter handles rejection of permanent roles.
Agency: always "Michael Page" (direct agency board).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.michael_page")

_BASE_URL = "https://www.michaelpage.co.uk"
_SEARCH_URL_TEMPLATE = "https://www.michaelpage.co.uk/jobs/{slug}"
_MAX_PAGES_DEFAULT = 3
_REQUEST_TIMEOUT = 30.0
_DEFAULT_KEYWORDS: list[str] = ["project-manager"]

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
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _keyword_to_slug(keyword: str) -> str:
    """Convert a keyword string to a URL-safe slug (spaces → hyphens, lowercase).

    # example: "project manager"        → "project-manager"
    # example: "contracts manager"      → "contracts-manager"
    # example: "document-controller"    → "document-controller"  (unchanged)
    """
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")


def _build_search_urls(
    keywords_list: list[str],
    max_pages: int = _MAX_PAGES_DEFAULT,
) -> list[tuple[str, str]]:
    """Return (url, keyword) pairs for all keywords × pages.

    Page 0 uses the base slug URL (no ?page= param); subsequent pages append
    ?page=N.  Only up to max_pages pages per keyword are queued.
    """
    result: list[tuple[str, str]] = []
    for kw in keywords_list:
        slug = _keyword_to_slug(kw)
        base = _SEARCH_URL_TEMPLATE.format(slug=slug)
        for page in range(max_pages):
            url = base if page == 0 else f"{base}?page={page}"
            result.append((url, kw))
    return result


def _get_text_after_icon(node) -> str | None:
    """Return stripped text from a node that begins with a FontAwesome <i> icon.

    The <i> element has no text content; `.text()` returns concatenated text
    of all descendants and text nodes.  The icon contributes nothing, so the
    result is the salary/location/contract text after the icon.
    """
    if node is None:
        return None
    text = node.text(strip=True)
    return text if text else None


def _find_cards(tree: HTMLParser) -> list:
    """Return job-tile elements from a search-results page."""
    return tree.css("li.views-row div.job-tile")


def _has_next_page(tree: HTMLParser) -> bool:
    """True when a 'next page' pager link is present."""
    return bool(tree.css("ul.pager__items a[rel=next]"))


def _card_to_raw_listing(card, page_url: str) -> RawListing | None:
    """Map one parsed card node to a RawListing.

    Returns None if title or listing ID is absent.

    Field map (confirmed 2026-06-17):
      div.job-title id attr      → source_listing_id
      div.job-title h3 a         → title + url (site-relative href)
      div.job-location           → location_raw (after icon)
      div.job-contract-type      → contract_type_raw (after icon)
      div.job-salary             → salary_raw (after icon; optional)
      div.job_advert__job-summary-text + div.job_advert__job-desc-bullet-points
                                 → description_raw (concatenated)
    """
    # --- Listing ID from div.job-title id attribute ---
    title_div = card.css_first("div.job-title")
    listing_id: str = (title_div.attributes.get("id", "") or "") if title_div else ""

    # --- Title + URL ---
    title_link = card.css_first("div.job-title h3 a")
    if title_link is None:
        return None
    title = title_link.text(strip=True)
    if not title:
        return None

    href = title_link.attributes.get("href", "") or ""
    listing_url = href if href.startswith("http") else urljoin(_BASE_URL, href)

    if not listing_id:
        # Fallback: extract numeric ID from the link id attr ("job-9282906")
        link_id = title_link.attributes.get("id", "") or ""
        m = re.match(r"job-(\d+)", link_id)
        if m:
            listing_id = m.group(1)
        else:
            listing_id = listing_url  # last resort

    # --- Location ---
    location_raw = _get_text_after_icon(card.css_first("div.job-location"))

    # --- Contract type ---
    contract_type_raw = _get_text_after_icon(card.css_first("div.job-contract-type"))

    # --- Salary (optional) ---
    salary_raw = _get_text_after_icon(card.css_first("div.job-salary"))

    # --- Description: summary paragraph + bullet points ---
    parts: list[str] = []
    summary = card.css_first("div.job_advert__job-summary-text")
    if summary:
        text = summary.text(strip=True)
        if text:
            parts.append(text)
    bullets = card.css_first("div.job_advert__job-desc-bullet-points")
    if bullets:
        text = bullets.text(strip=True)
        if text:
            parts.append(text)
    description_raw: str | None = "\n".join(parts) if parts else None

    return RawListing(
        source="michael_page",
        source_listing_id=listing_id,
        url=listing_url,
        title=title,
        employer=None,
        agency="Michael Page",
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=None,  # not available in MP search cards
        salary_raw=salary_raw,
        contract_type_raw=contract_type_raw,
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
        logger.exception("michael_page: HTMLParser failed for %s", page_url)
        return []

    cards = _find_cards(tree)
    if not cards:
        logger.warning(
            "michael_page: no job cards found in HTML (%s). "
            "DOM may have changed.",
            page_url,
        )
        return []

    listings: list[RawListing] = []
    for card in cards:
        listing = _card_to_raw_listing(card, page_url)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "michael_page: %s → %d listing(s) from %d card(s).",
        page_url,
        len(listings),
        len(cards),
    )
    return listings


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

class MichaelPageAdapter(SourceAdapter):
    """Adapter for Michael Page UK (michaelpage.co.uk).

    Fetches contract/interim PM and engineering listings via Drupal HTML scraping.
    Returns all listings regardless of contract type; the pipeline's
    passes_contract() filter rejects permanent roles.
    """

    name = "michael_page"

    def __init__(
        self,
        crawl_delay: int = 5,
        keywords_list: list[str] | None = None,
        max_pages_per_query: int = _MAX_PAGES_DEFAULT,
    ) -> None:
        self.crawl_delay = crawl_delay
        self.keywords_list = keywords_list or _DEFAULT_KEYWORDS
        self.max_pages_per_query = max_pages_per_query

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch listings from Michael Page for all configured keywords.

        Iterates keyword slugs, paginates up to max_pages_per_query,
        deduplicates by source_listing_id, and respects crawl_delay between
        each page request.

        404 responses are logged as warnings and silently skipped; the adapter
        never raises.
        """
        all_listings: list[RawListing] = []
        seen_ids: set[str] = set()

        url_queue = _build_search_urls(self.keywords_list, self.max_pages_per_query)

        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            for idx, (url, keyword) in enumerate(url_queue):
                try:
                    response = await client.get(url)
                except Exception as exc:
                    logger.warning(
                        "michael_page: HTTP error for %s (%s) — skipping.",
                        url,
                        exc,
                    )
                    if idx < len(url_queue) - 1:
                        await asyncio.sleep(self.crawl_delay)
                    continue

                if response.status_code == 404:
                    page_num = url.split("?page=")[-1] if "?page=" in url else "0"
                    if page_num == "0":
                        logger.warning(
                            "michael_page: 404 for keyword=%r slug=%r — "
                            "no Drupal path configured for this keyword.",
                            keyword,
                            _keyword_to_slug(keyword),
                        )
                    else:
                        logger.debug(
                            "michael_page: 404 for keyword=%r page=%s — "
                            "no more pages (end of results).",
                            keyword,
                            page_num,
                        )
                    if idx < len(url_queue) - 1:
                        await asyncio.sleep(self.crawl_delay)
                    continue

                if response.status_code != 200:
                    logger.warning(
                        "michael_page: HTTP %d for %s — skipping.",
                        response.status_code,
                        url,
                    )
                    if idx < len(url_queue) - 1:
                        await asyncio.sleep(self.crawl_delay)
                    continue

                page_listings = _parse_html(response.text, url)

                new_count = 0
                for listing in page_listings:
                    if listing.source_listing_id not in seen_ids:
                        seen_ids.add(listing.source_listing_id)
                        all_listings.append(listing)
                        new_count += 1

                logger.debug(
                    "michael_page: keyword=%r page=%s → %d new listing(s) "
                    "(%d on page, %d total so far).",
                    keyword,
                    url.split("?page=")[-1] if "?page=" in url else "0",
                    new_count,
                    len(page_listings),
                    len(all_listings),
                )

                if idx < len(url_queue) - 1:
                    await asyncio.sleep(self.crawl_delay)

        logger.info(
            "michael_page: fetch complete — %d listing(s) across %d keyword(s).",
            len(all_listings),
            len(self.keywords_list),
        )
        return all_listings


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import asyncio
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    async def _smoke() -> None:
        adapter = MichaelPageAdapter(
            crawl_delay=5,
            keywords_list=["project-manager", "project-engineer", "contracts-manager"],
            max_pages_per_query=2,
        )
        listings = await adapter.fetch()
        print(f"\nSmoke test: {len(listings)} listing(s) returned.")
        interim_temp = [l for l in listings if l.contract_type_raw in ("Interim", "Temporary")]
        perm = [l for l in listings if l.contract_type_raw == "Permanent"]
        day_rate = [l for l in listings if l.salary_raw and "per day" in (l.salary_raw or "")]
        print(f"  Interim/Temporary: {len(interim_temp)}")
        print(f"  Permanent:         {len(perm)}")
        print(f"  Day-rate salary:   {len(day_rate)}")
        for l in day_rate[:3]:
            print(f"    {l.title[:40]} | {l.salary_raw} | {l.location_raw}")

    asyncio.run(_smoke())
