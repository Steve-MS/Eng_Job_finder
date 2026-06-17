"""Manpower Group adapter — careers.manpowergroup.co.uk HTML scrape.

Confirmed DOM structure (live recon 2026-06-17):
  Card:      <li class="job-result-item"> inside <ul class="results-list clearfix">
  Title:     div.job-title a  (text; href is site-relative)
  URL:       div.job-title a[href]  → absolute after prepending _BASE_URL
  ID:        regex r'-([0-9]+)$' on href slug (numeric suffix)
  Location:  li.results-job-location  (text)
  Salary:    li.results-salary  (text; textual description, not always numeric)
  Posted:    li.results-posted-at  (relative text: "Posted 12 days ago")
  Desc:      p.job-description  (short snippet)
  Agency:    figure.recruiter-figure img[alt]  (recruiter/brand name)

Contract type: NOT exposed in search cards.  contract_type_raw is always None.
  The extractor's parse_contract_type(None, title, description) scans the
  description snippet for perm/contract signals; ambiguous cases default to
  "contract" per the conservative extractor policy.

Search URL:
  https://careers.manpowergroup.co.uk/jobs?sort_type=relevance&query={keywords}&radius=1600km

Pagination: page 1 = no page param; subsequent pages use &page=N (1-indexed, N≥2).
  Next-page link selector: a[rel=next] — href contains &page=N.

robots.txt (368 chars, 2026-06-17):
  Disallows /admin/*, /sa/*, /api/*.  /jobs is fully allowed.
  No Crawl-delay directive.  We use 5 s between requests out of courtesy.

Cloudflare: CDN-only (not blocking) — live recon returned 200 OK with full content.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urljoin

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.manpower_group")

_BASE_URL = "https://careers.manpowergroup.co.uk"
_SEARCH_URL_TEMPLATE = (
    "https://careers.manpowergroup.co.uk/jobs"
    "?sort_type=relevance&query={query}&radius=1600km"
)
_MAX_PAGES_DEFAULT = 5
_REQUEST_TIMEOUT = 30.0
_DEFAULT_KEYWORDS: list[str] = ["project manager"]

# Regex to extract the numeric job ID from the URL slug's trailing segment.
# example: "/job/senior-project-manager-1234567"  → "1234567"
_ID_FROM_SLUG_RE = re.compile(r"-(\d+)$")

# Regex to parse relative posted-at text produced by the site.
# example: "Posted 1 day ago"        → days=1
# example: "Posted 12 days ago"      → days=12
# example: "Posted about 1 month ago"→ days=30
# example: "Posted about 2 months ago" → days=60
_POSTED_DAYS_RE = re.compile(
    r"posted\s+(?:about\s+)?(\d+)\s+(day|days|week|weeks|month|months)\s+ago",
    re.IGNORECASE,
)

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

def _build_search_url(keyword: str, page: int) -> str:
    """Return the search URL for a keyword and (1-indexed) page number.

    Page 1 uses no page param; pages ≥2 append &page=N.

    # example: ("project manager", 1) → "…?sort_type=relevance&query=project+manager&radius=1600km"
    # example: ("project manager", 2) → "…?sort_type=relevance&query=project+manager&radius=1600km&page=2"
    """
    url = _SEARCH_URL_TEMPLATE.format(query=quote_plus(keyword))
    if page >= 2:
        url = f"{url}&page={page}"
    return url


def _parse_posted_at(text: str | None) -> datetime | None:
    """Convert relative posted text to a UTC-aware datetime.

    Returns None when the text cannot be parsed.

    # example: "Posted 1 day ago"          → now() - 1 day
    # example: "Posted 12 days ago"         → now() - 12 days
    # example: "Posted about 1 month ago"   → now() - 30 days
    # example: "Posted about 3 months ago"  → now() - 90 days
    # example: "Posted 2 weeks ago"         → now() - 14 days
    # example: None                         → None
    """
    if not text:
        return None
    m = _POSTED_DAYS_RE.search(text)
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("day", "days"):
        delta = timedelta(days=value)
    elif unit in ("week", "weeks"):
        delta = timedelta(weeks=value)
    elif unit in ("month", "months"):
        delta = timedelta(days=value * 30)
    else:
        return None
    return datetime.now(timezone.utc) - delta


def _card_to_raw_listing(card, source: str = "manpower_group") -> RawListing | None:
    """Map one parsed li.job-result-item node to a RawListing.

    Returns None if title or listing URL is absent.

    Field map (confirmed 2026-06-17):
      div.job-title a          → title (text) + url (href)
      href slug suffix         → source_listing_id (last numeric segment)
      li.results-job-location  → location_raw
      li.results-salary        → salary_raw (optional)
      li.results-posted-at     → posted_at (parsed from relative text)
      p.job-description        → description_raw (short snippet)
      figure.recruiter-figure img[alt] → agency
    """
    # --- Title + URL ---
    title_a = card.css_first("div.job-title a")
    if title_a is None:
        return None
    title = title_a.text(strip=True)
    if not title:
        return None

    href = title_a.attributes.get("href", "") or ""
    if not href:
        return None
    url = href if href.startswith("http") else urljoin(_BASE_URL, href)

    # --- Listing ID: last hyphen-digits segment of slug ---
    m = _ID_FROM_SLUG_RE.search(href)
    listing_id = m.group(1) if m else url  # fall back to full URL as dedup key

    # --- Location ---
    loc_node = card.css_first("li.results-job-location")
    location_raw: str | None = loc_node.text(strip=True) if loc_node else None
    if not location_raw:
        location_raw = None

    # --- Salary (often textual: "Attractive salary…") ---
    sal_node = card.css_first("li.results-salary")
    salary_raw: str | None = sal_node.text(strip=True) if sal_node else None
    if not salary_raw:
        salary_raw = None

    # --- Posted date (relative text → approximate UTC datetime) ---
    posted_node = card.css_first("li.results-posted-at")
    posted_raw_text = posted_node.text(strip=True) if posted_node else None
    posted_at = _parse_posted_at(posted_raw_text)

    # --- Description snippet ---
    desc_node = card.css_first("p.job-description")
    description_raw: str | None = desc_node.text(strip=True) if desc_node else None
    if not description_raw:
        description_raw = None

    # --- Agency (recruiter brand from img alt) ---
    recruiter_img = card.css_first("figure.recruiter-figure img")
    agency: str | None = None
    if recruiter_img is not None:
        alt = recruiter_img.attributes.get("alt", "") or ""
        agency = alt.strip() if alt.strip() else None

    return RawListing(
        source=source,
        source_listing_id=listing_id,
        url=url,
        title=title,
        employer=None,
        agency=agency,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw=None,  # not exposed in search cards
        metadata={
            "detail_fetched": False,
            "posted_raw": posted_raw_text,
        },
    )


def _has_next_page(tree: HTMLParser) -> bool:
    """True when a 'next page' link is present in the pagination bar."""
    return bool(tree.css("a[rel=next]"))


def _parse_html(html: str, page_url: str, source: str = "manpower_group") -> list[RawListing]:
    """Parse a raw HTML search-results page and return RawListings.

    Extracted as a standalone function so fixture-based unit tests can call it
    directly without making HTTP requests.
    """
    try:
        tree = HTMLParser(html)
    except Exception:
        logger.exception("manpower_group: HTMLParser failed for %s", page_url)
        return []

    cards = tree.css("li.job-result-item")
    if not cards:
        logger.warning(
            "manpower_group: no job cards found in HTML (%s). "
            "DOM may have changed.",
            page_url,
        )
        return []

    listings: list[RawListing] = []
    for card in cards:
        listing = _card_to_raw_listing(card, source=source)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "manpower_group: %s → %d listing(s) from %d card(s).",
        page_url,
        len(listings),
        len(cards),
    )
    return listings


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

class ManpowerGroupAdapter(SourceAdapter):
    """Adapter for Manpower Group UK (careers.manpowergroup.co.uk).

    Fetches job listings via HTML scraping of the public search endpoint.
    Returns all listings regardless of contract type; the pipeline's
    passes_contract() filter rejects permanent roles (using description
    signals since contract_type_raw is not exposed in search cards).

    Multi-query via ``keywords_list``.  Paginates up to ``max_pages_per_query``
    pages per keyword (1-indexed, page 1 = no page param).  Deduplicates
    within-source by ``source_listing_id`` (numeric job ID from URL slug).
    """

    name = "manpower_group"

    def __init__(
        self,
        crawl_delay: int = 5,
        keywords_list: list[str] | None = None,
        max_pages_per_query: int = _MAX_PAGES_DEFAULT,
    ) -> None:
        self.crawl_delay = crawl_delay
        self.keywords_list = keywords_list or list(_DEFAULT_KEYWORDS)
        self.max_pages_per_query = max_pages_per_query

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch listings from Manpower Group for all configured keywords.

        Iterates each keyword, paginates up to max_pages_per_query, stops
        early when no next-page link is present.  Deduplicates by
        source_listing_id across all keyword queries.  Respects crawl_delay
        between every HTTP request.  Never raises; returns partial results
        on error.
        """
        all_listings: list[RawListing] = []
        seen_ids: set[str] = set()
        request_count = 0

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for keyword in self.keywords_list:
                    for page in range(1, self.max_pages_per_query + 1):
                        url = _build_search_url(keyword, page)

                        if request_count > 0:
                            await asyncio.sleep(self.crawl_delay)

                        try:
                            response = await client.get(url)
                            request_count += 1
                        except Exception as exc:
                            logger.warning(
                                "manpower_group: HTTP error for %s (%s) — skipping.",
                                url,
                                exc,
                            )
                            break

                        if response.status_code != 200:
                            logger.warning(
                                "manpower_group: HTTP %d for %s — skipping keyword.",
                                response.status_code,
                                url,
                            )
                            break

                        tree = HTMLParser(response.text)
                        page_listings = _parse_html(response.text, url)

                        new_count = 0
                        for listing in page_listings:
                            if listing.source_listing_id not in seen_ids:
                                # Skip only when we have a definite pre-cutoff date.
                                if since and listing.posted_at and listing.posted_at < since:
                                    continue
                                seen_ids.add(listing.source_listing_id)
                                all_listings.append(listing)
                                new_count += 1

                        logger.debug(
                            "manpower_group: keyword=%r page=%d → %d new listing(s) "
                            "(%d on page, %d total so far).",
                            keyword,
                            page,
                            new_count,
                            len(page_listings),
                            len(all_listings),
                        )

                        if not _has_next_page(tree):
                            break

        except Exception:
            logger.exception(
                "manpower_group: fetch failed — returning partial results (%d so far).",
                len(all_listings),
            )
            return all_listings

        logger.info(
            "manpower_group: fetch complete — %d listing(s) across %d keyword(s).",
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
        adapter = ManpowerGroupAdapter(
            crawl_delay=5,
            keywords_list=[
                "project manager engineering",
                "project engineer mechanical",
                "project director",
                "document controller",
                "assurance engineer",
            ],
            max_pages_per_query=3,
        )
        listings = await adapter.fetch()
        print(f"\nSmoke test: {len(listings)} listing(s) returned.")
        day_rate = [l for l in listings if l.salary_raw and "per day" in (l.salary_raw or "").lower()]
        print(f"  With day-rate salary: {len(day_rate)}")
        print(f"  Sample listings:")
        for l in listings[:5]:
            print(f"    [{l.source_listing_id}] {l.title[:50]} | {l.location_raw} | {l.agency}")

    asyncio.run(_smoke())
