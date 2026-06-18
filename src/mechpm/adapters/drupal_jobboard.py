"""DrupalJobBoardAdapter — shared adapter for UK construction/civil job boards.

Handles 3 sites via config.  Despite the class name, two use Drupal and one
uses the Jobiqo Next.js platform; all share the same URL search pattern and
adapter interface.

Platform A — Jobiqo (Next.js / SSR)
  Site: Building4Jobs (www.building4jobs.com)

  Discovery (live recon 2026-06-18):
  - Site runs Next.js with Jobiqo job-board engine (platform owner: jobiqo).
  - Jobs embedded in ``__NEXT_DATA__`` JSON under:
      props.pageProps.data.jobs.pages[]       — job items for this page
      props.pageProps.data.jobs.result_count  — total result count
  - Pagination: ?page=N where N is 1-indexed.
    page=0 and page=1 both return the first 10 items; page=2 is the second
    batch of 10.  We iterate starting at page=1 and stop when the pages
    array is empty.
  - Page size: 10 items.
  - No bot protection; robots.txt allows /jobs.

  Field map (pages[]):
    id               → source_listing_id (integer → str)
    title            → title
    url.path         → relative path (prepend base_url for absolute URL)
    organization     → employer (string)
    address          → location_raw (string)
    salaryRange.label→ salary_raw (Term object; optional)
    published        → posted_at (MM/DD/YYYY HH:MM:SS — US date format)
    [duration is posting duration in days, not employment type → ignored]

Platform B — Drupal Epiq Jobs (server-rendered HTML)
  Sites:
    New Civil Engineer Careers (www.newcivilengineercareers.com)
    Careers in Construction    (www.careersinconstruction.com)

  Discovery (live recon 2026-06-18):
  - Both sites run an identical Drupal Epiq Jobs theme with the same CSS selectors.
  - Jobs rendered as ``div.views-row > article[id^="node-"]`` inside ``div.view-content``.
  - Pagination: ?page=N (0-indexed).  page=0 = first page.  Stop when no rows.
  - Page size: ~10–20 (Drupal view setting per site).
  - No bot protection; robots.txt allows /jobs.

  Field map (per article card):
    article[id]                                       → source_listing_id ("node-41157" → "41157")
    h2.node__title a.recruiter-job-link               → title (text) + url (abs href)
    div.description span.date                         → posted_at ("D Mon YYYY,")
    span.recruiter-company-profile-job-organization a → employer
    div.location span                                 → location_raw
    [no salary or contract type in search cards]

Pagination strategy (both platforms)
  For each keyword in keywords_list:
    - Iterate pages from start_page up to start_page + max_pages_per_query - 1.
    - Stop early when a page returns zero listings.
  Union-dedup by source_listing_id across all keyword/page fetches.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.drupal_jobboard")

_REQUEST_TIMEOUT = 30.0
_DEFAULT_MAX_PAGES = 5
_PAGE_DELAY_SECONDS = 5

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
# Jobiqo (Building4Jobs) helpers
# ---------------------------------------------------------------------------

def _extract_next_data(html: str) -> dict:
    """Extract the ``__NEXT_DATA__`` JSON object from a Next.js page.

    The data is embedded as:
      ``<script id="__NEXT_DATA__" type="application/json">{...}</script>``
    """
    start_tag = '<script id="__NEXT_DATA__" type="application/json">'
    start_idx = html.find(start_tag)
    if start_idx < 0:
        return {}
    json_start = start_idx + len(start_tag)
    json_end = html.find("</script>", json_start)
    if json_end < 0:
        return {}
    try:
        return json.loads(html[json_start:json_end])
    except json.JSONDecodeError:
        logger.debug("drupal_jobboard: __NEXT_DATA__ JSON parse failed.")
        return {}


def _parse_jobiqo_date(raw: str | None) -> datetime | None:
    """Parse a Jobiqo ``published`` date string.

    Confirmed format from live data: ISO 8601 with timezone offset.
    Example: ``"2026-06-10T11:09:07+01:00"`` → 2026-06-10 10:09:07 UTC.
    Also handles bare date-only strings as a fallback.
    """
    if not raw:
        return None
    raw = raw.strip()
    # Try full ISO 8601 with optional timezone
    m = re.match(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(Z|[+-]\d{2}:\d{2}|[+-]\d{4})?",
        raw,
    )
    if m:
        dt_str = m.group(1)
        tz_str = m.group(2) or "Z"
        try:
            dt_naive = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
        else:
            if tz_str in ("Z", "+00:00", "+0000"):
                return dt_naive.replace(tzinfo=timezone.utc)
            # Parse the offset manually and convert to UTC.
            tz_str_clean = tz_str.replace(":", "")  # +01:00 → +0100
            try:
                dt_with_tz = datetime.strptime(dt_str + tz_str_clean, "%Y-%m-%dT%H:%M:%S%z")
                return dt_with_tz.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
            except ValueError:
                return dt_naive.replace(tzinfo=timezone.utc)
    # Fallback: date-only
    plain = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if plain:
        try:
            return datetime.strptime(plain.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    logger.debug("drupal_jobboard: could not parse Jobiqo date %r", raw)
    return None


def _jobiqo_item_to_raw_listing(
    item: dict,
    source_name: str,
    base_url: str,
) -> RawListing | None:
    """Map one Jobiqo job dict to a RawListing.

    Returns None if the item lacks a required id or title.
    """
    job_id = item.get("id")
    if not job_id:
        return None

    title = (item.get("title") or "").strip()
    if not title:
        return None

    url_obj = item.get("url") or {}
    url_path = url_obj.get("path", "") if isinstance(url_obj, dict) else ""
    listing_url = f"{base_url}{url_path}" if url_path else ""

    employer = (item.get("organization") or "").strip() or None

    # address is a list in the Jobiqo JSON; take the first element.
    address_raw = item.get("address")
    if isinstance(address_raw, list):
        address_raw = address_raw[0] if address_raw else ""
    location_raw = (address_raw or "").strip() or None

    # salaryRange is a list of Term objects; take the first label.
    salary_obj = item.get("salaryRange")
    salary_raw: str | None = None
    if isinstance(salary_obj, list) and salary_obj:
        salary_obj = salary_obj[0]
    if isinstance(salary_obj, dict):
        label = salary_obj.get("label") or ""
        salary_raw = label.strip() or None

    posted_at = _parse_jobiqo_date(item.get("published"))

    return RawListing(
        source=source_name,
        source_listing_id=str(job_id),
        url=listing_url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=None,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw=None,
        metadata={
            "platform": "jobiqo",
            "detail_fetched": False,
        },
    )


def parse_jobiqo_html(
    html: str,
    page_url: str,
    source_name: str,
    base_url: str,
) -> list[RawListing]:
    """Parse a Jobiqo/Next.js search results page and return RawListings.

    Extracted as a standalone function so fixture-based unit tests can call
    it directly without any network I/O.
    """
    data = _extract_next_data(html)
    if not data:
        logger.warning(
            "drupal_jobboard[%s]: no __NEXT_DATA__ found in %s",
            source_name,
            page_url,
        )
        return []

    try:
        jobs_block = data["props"]["pageProps"]["data"]["jobs"]
        items: list[dict] = jobs_block.get("pages", [])
    except (KeyError, TypeError):
        logger.warning(
            "drupal_jobboard[%s]: unexpected __NEXT_DATA__ structure in %s",
            source_name,
            page_url,
        )
        return []

    listings: list[RawListing] = []
    for item in items:
        listing = _jobiqo_item_to_raw_listing(item, source_name, base_url)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "drupal_jobboard[%s]: %s → %d listing(s) from %d item(s).",
        source_name,
        page_url,
        len(listings),
        len(items),
    )
    return listings


# ---------------------------------------------------------------------------
# Drupal Epiq Jobs helpers (NCE / CIC)
# ---------------------------------------------------------------------------

def _parse_epiq_date(raw: str | None) -> datetime | None:
    """Parse a Drupal Epiq Jobs posted-date string.

    Confirmed formats: ``"D Mon YYYY,"`` or ``"D Mon YYYY"``.
    Examples: ``"16 May 2025,"`` → 2025-05-16, ``"17 Jun 2026,"`` → 2026-06-17.
    """
    if not raw:
        return None
    cleaned = raw.strip().rstrip(",").strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.debug("drupal_jobboard: could not parse Epiq date %r", raw)
    return None


def _epiq_card_to_raw_listing(
    card,
    page_url: str,
    source_name: str,
    base_url: str,
) -> RawListing | None:
    """Map one Drupal Epiq Jobs article card to a RawListing.

    Returns None if title is absent.
    """
    # --- Listing ID from article id="node-{id}" ---
    art_id = card.attributes.get("id", "") or ""
    m = re.match(r"node-(\d+)", art_id)
    listing_id: str = m.group(1) if m else ""

    # --- Title + URL from h2.node__title a.recruiter-job-link ---
    title_link = card.css_first("h2.node__title a.recruiter-job-link")
    if title_link is None:
        title_link = card.css_first(".node__title a")
    if title_link is None:
        return None

    # Use the link's title attribute first (avoids any badge text bleed-through),
    # then fall back to inner text.
    title = (title_link.attributes.get("title") or "").strip()
    if not title:
        title = title_link.text(strip=True)
    if not title:
        return None

    href = title_link.attributes.get("href", "") or ""
    listing_url = href if href.startswith("http") else f"{base_url}{href}"

    if not listing_id:
        # Fallback: extract numeric suffix from URL slug, e.g. "/job/pm-director-123" → "123"
        slug_m = re.search(r"-(\d+)/?$", href)
        listing_id = slug_m.group(1) if slug_m else listing_url

    # --- Posted date from div.description span.date ---
    date_span = card.css_first("div.description span.date")
    date_text = date_span.text(strip=True) if date_span else None
    posted_at = _parse_epiq_date(date_text)

    # --- Employer from span.recruiter-company-profile-job-organization ---
    employer_link = card.css_first(
        "span.recruiter-company-profile-job-organization a"
    )
    if employer_link is not None:
        employer: str | None = employer_link.text(strip=True) or None
    else:
        employer_span = card.css_first(
            "span.recruiter-company-profile-job-organization"
        )
        employer = employer_span.text(strip=True) if employer_span else None
    if employer:
        employer = employer or None  # empty string → None

    # --- Location from div.location span ---
    location_node = card.css_first("div.location span")
    if location_node is None:
        location_node = card.css_first("div.location")
    location_raw: str | None = None
    if location_node is not None:
        location_raw = location_node.text(strip=True) or None

    return RawListing(
        source=source_name,
        source_listing_id=listing_id,
        url=listing_url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=None,
        posted_at=posted_at,
        salary_raw=None,
        contract_type_raw=None,
        metadata={
            "platform": "drupal_epiq",
            "detail_fetched": False,
        },
    )


def parse_drupal_epiq_html(
    html: str,
    page_url: str,
    source_name: str,
    base_url: str,
) -> list[RawListing]:
    """Parse a Drupal Epiq Jobs search results page and return RawListings.

    Extracted as a standalone function so fixture-based unit tests can call
    it directly without any network I/O.
    """
    try:
        tree = HTMLParser(html)
    except Exception:
        logger.exception(
            "drupal_jobboard[%s]: HTMLParser failed for %s",
            source_name,
            page_url,
        )
        return []

    # Primary selector: article cards inside view rows.
    cards = tree.css("div.views-row article")
    if not cards:
        # Fallback: any article with the node-job class
        cards = tree.css("article.node-job")

    if not cards:
        logger.debug(
            "drupal_jobboard[%s]: no job cards found in %s.",
            source_name,
            page_url,
        )
        return []

    listings: list[RawListing] = []
    for card in cards:
        listing = _epiq_card_to_raw_listing(card, page_url, source_name, base_url)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "drupal_jobboard[%s]: %s → %d listing(s) from %d card(s).",
        source_name,
        page_url,
        len(listings),
        len(cards),
    )
    return listings


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

class DrupalJobBoardAdapter(SourceAdapter):
    """Shared adapter for UK construction/civil job boards.

    Handles two distinct platforms via the ``platform`` config key:

    - ``"jobiqo"``      — Next.js/Jobiqo sites (Building4Jobs).
                          Jobs in ``__NEXT_DATA__`` JSON; 1-indexed pagination.
    - ``"drupal_epiq"`` — Drupal Epiq Jobs sites (NCE Careers, Careers in
                          Construction). Server-rendered HTML; 0-indexed pagination.

    One adapter instance per site, configured via config.toml.  All 3 sites
    use the same keyword/page URL pattern:
      ``/jobs?search_api_views_fulltext={keyword}&page={N}``

    robots.txt: all 3 sites allow crawling of /jobs paths.
    Crawl-delay: ``crawl_delay`` seconds between every HTTP request.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        platform: str = "drupal_epiq",
        keywords_list: list[str] | None = None,
        crawl_delay: int = _PAGE_DELAY_SECONDS,
        max_pages_per_query: int = _DEFAULT_MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.platform = platform
        self.keywords_list: list[str] = keywords_list or ["project manager"]
        self.crawl_delay = crawl_delay
        self.max_pages_per_query = max_pages_per_query

    def _start_page(self) -> int:
        """Return the first page number for the chosen platform.

        - Jobiqo: 1-indexed (page=0 and page=1 are both the first page; use 1).
        - Drupal Epiq: 0-indexed (page=0 is the first page).
        """
        return 1 if self.platform == "jobiqo" else 0

    def _build_page_url(self, keyword: str, page_num: int) -> str:
        """Build a search URL for the given keyword and page number."""
        encoded_kw = quote_plus(keyword)
        return (
            f"{self.base_url}/jobs"
            f"?search_api_views_fulltext={encoded_kw}&page={page_num}"
        )

    def _parse_page(self, html: str, page_url: str) -> list[RawListing]:
        """Dispatch to the appropriate platform parser."""
        if self.platform == "jobiqo":
            return parse_jobiqo_html(html, page_url, self.name, self.base_url)
        return parse_drupal_epiq_html(html, page_url, self.name, self.base_url)

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch job listings by iterating keywords and paginating.

        - Iterates each entry in ``keywords_list``.
        - Paginates up to ``max_pages_per_query`` pages per keyword.
        - Stops a keyword early when a page returns zero listings.
        - Deduplicates by ``source_listing_id`` across all keyword/page fetches.
        - Applies ``since`` filter on ``posted_at`` (best-effort; entries
          without a date are always included).
        - Returns [] and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}
        first_request = True
        start_page = self._start_page()

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for keyword in self.keywords_list:
                    for page_num in range(
                        start_page, start_page + self.max_pages_per_query
                    ):
                        url = self._build_page_url(keyword, page_num)

                        if not first_request:
                            await asyncio.sleep(self.crawl_delay)
                        first_request = False

                        page_listings = await self._fetch_page(client, url)

                        if not page_listings:
                            logger.debug(
                                "drupal_jobboard[%s]: empty page %d for "
                                "keyword %r — stopping pagination.",
                                self.name,
                                page_num,
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
                            "drupal_jobboard[%s]: keyword=%r page=%d "
                            "→ %d on page, %d new unique.",
                            self.name,
                            keyword,
                            page_num,
                            len(page_listings),
                            new_count,
                        )

        except Exception:
            logger.exception(
                "drupal_jobboard[%s]: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                self.name,
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "drupal_jobboard[%s]: fetched %d unique listing(s) "
            "(platform=%s, keywords=%d, max_pages=%d, since=%s).",
            self.name,
            len(listings),
            self.platform,
            len(self.keywords_list),
            self.max_pages_per_query,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> list[RawListing]:
        """Fetch and parse a single search-results page.

        Returns [] on HTTP or parse failure; logs a warning.
        """
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning(
                "drupal_jobboard[%s]: request error for %s: %s",
                self.name,
                url,
                exc,
            )
            return []

        if response.status_code != 200:
            logger.warning(
                "drupal_jobboard[%s]: HTTP %d for %s",
                self.name,
                response.status_code,
                url,
            )
            return []

        return self._parse_page(response.text, url)


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
        sites = [
            DrupalJobBoardAdapter(
                name="building4jobs",
                base_url="https://www.building4jobs.com",
                platform="jobiqo",
                keywords_list=["project manager", "project engineer"],
                crawl_delay=5,
                max_pages_per_query=3,
            ),
            DrupalJobBoardAdapter(
                name="nce_careers",
                base_url="https://www.newcivilengineercareers.com",
                platform="drupal_epiq",
                keywords_list=["project manager", "project engineer"],
                crawl_delay=5,
                max_pages_per_query=3,
            ),
            DrupalJobBoardAdapter(
                name="careers_in_construction",
                base_url="https://www.careersinconstruction.com",
                platform="drupal_epiq",
                keywords_list=["project manager", "project engineer"],
                crawl_delay=5,
                max_pages_per_query=3,
            ),
        ]
        for adapter in sites:
            listings = await adapter.fetch()
            print(
                f"\n{adapter.name}: {len(listings)} listing(s) "
                f"(platform={adapter.platform})"
            )
            for lst in listings[:3]:
                print(
                    f"  [{lst.source_listing_id}] {lst.title[:50]} "
                    f"| {lst.employer} | {lst.location_raw}"
                )

    asyncio.run(_smoke())
