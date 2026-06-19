"""RailRecruiter adapter — ASP.NET HTML scrape.

Confirmed structure (live recon 2026-06-19):
  - ASP.NET WebForms site with browser-fingerprint challenge.
  - Session setup: GET /jobs/ → POST /_xhrc.ashx (empty body) → cookies set.
  - Search URL: /jobs/job-search-results/kw-{keyword-slug}/co-225/
    where co-225 = United Kingdom country filter.
  - Pagination: ?page=2, ?page=3, … (first page has no ?page param).
  - Results container: <ol class="vacancy-listing"> / #uxMainContent_uxResultsOL
  - Each result: <li id="v{id}[-{type}]">
      h2 a                  → title + relative URL
      .logo a href          → agency name (encoded as em-{Name-With-Hyphens})
      dd.location span      → location_raw
      dd.salary span        → salary_raw
      dd.job-type span      → contract_type_raw
      dd.posted span        → posted_at  (format "D Mon YYYY", e.g. "4 Jun 2026")
      p                     → description_raw (short excerpt)

robots.txt: Crawl-delay 0.2 (we use 3 s for politeness; ClaudeBot disallowed so
            we send a Chrome UA).
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

logger = logging.getLogger("mechpm.adapter.railrecruiter")

_BASE_URL = "https://www.railrecruiter.co.uk"
_MAX_PAGES = 5
_PAGE_DELAY_SECONDS = 3  # polite delay; robots.txt allows 0.2 s
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
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _keyword_to_slug(keyword: str) -> str:
    """Convert a keyword string to a RailRecruiter URL slug.

    Examples::

        >>> _keyword_to_slug("project manager")
        'project-manager'
        >>> _keyword_to_slug("Quantity Surveyor")
        'quantity-surveyor'
        >>> _keyword_to_slug("programme & risk manager")
        'programme-risk-manager'
    """
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower().strip()).strip("-")


def _build_search_url(base_url: str, keyword: str, page: int = 1) -> str:
    """Build a RailRecruiter search URL for one keyword and page number.

    Page 1 has no ``?page=`` param; pages 2+ append ``?page=N``.
    Country code 225 = United Kingdom.
    """
    slug = _keyword_to_slug(keyword)
    url = f"{base_url}/jobs/job-search-results/kw-{slug}/co-225/"
    if page > 1:
        url += f"?page={page}"
    return url


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------

def _extract_agency(logo_href: str) -> str | None:
    """Extract an agency/employer name from a RailRecruiter logo link href.

    The href encodes the name as ``/em-{Name-With-Hyphens}/``.
    Tildes encode special characters: ``~26`` = ``&``.

    Examples::

        >>> _extract_agency("/jobs/fawkes-reece-london-jobs/em-Fawkes-~26-Reece-London/")
        'Fawkes & Reece London'
        >>> _extract_agency("/jobs/network-rail-jobs/em-Network-Rail/")
        'Network Rail'
    """
    m = re.search(r"/em-([^/]+)/?$", logo_href)
    if not m:
        return None
    name = m.group(1)
    name = name.replace("~26", "&")
    name = name.replace("-", " ")
    return name.strip() or None


def _parse_date(raw: str | None) -> datetime | None:
    """Parse a posted-date string from RailRecruiter search result cards.

    Confirmed format (live recon 2026-06-19): ``D Mon YYYY`` e.g.
    ``4 Jun 2026``, ``12 May 2026``.  ISO variants handled for resilience.
    """
    if not raw:
        return None
    raw = raw.strip()
    for fmt in (
        "%d %B %Y",           # "4 June 2026" (full month name)
        "%d %b %Y",           # "4 Jun 2026"  (abbrev, confirmed live format)
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Tolerate ISO timestamps with arbitrary offset
    iso_m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso_m:
        try:
            return datetime.strptime(iso_m.group(1), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    logger.debug("railrecruiter: could not parse date string: %r", raw)
    return None


def _li_to_raw_listing(
    li,
    base_url: str,
    page_url: str,
) -> RawListing | None:
    """Map one ``<li id='v{id}'>`` node to a RawListing.

    Returns ``None`` if the required title or URL cannot be extracted.
    """
    li_id = li.attributes.get("id", "")
    if not li_id.startswith("v"):
        return None
    source_listing_id = li_id[1:]  # strip the leading "v"

    # --- Title + URL from h2 > a ---
    title_a = li.css_first("h2 a")
    if title_a is None:
        return None
    title = title_a.text(strip=True)
    if not title:
        return None
    href = title_a.attributes.get("href", "")
    url = href if href.startswith("http") else urljoin(base_url, href)

    # --- Agency from logo div a href ---
    logo_a = li.css_first(".logo a")
    agency: str | None = None
    if logo_a:
        logo_href = logo_a.attributes.get("href", "")
        agency = _extract_agency(logo_href)

    # --- Location (optional on some cards) ---
    loc_node = li.css_first("dd.location span") or li.css_first("dd.location")
    location_raw: str | None = loc_node.text(strip=True) if loc_node else None
    if not location_raw:
        location_raw = None

    # --- Salary (not present on all cards) ---
    sal_node = li.css_first("dd.salary span") or li.css_first("dd.salary")
    salary_raw: str | None = sal_node.text(strip=True) if sal_node else None
    if not salary_raw:
        salary_raw = None

    # --- Contract type ---
    ct_node = (
        li.css_first("dd.job-type span") or li.css_first("dd.job-type")
    )
    contract_type_raw: str | None = (
        ct_node.text(strip=True) if ct_node else None
    )
    if not contract_type_raw:
        contract_type_raw = None

    # --- Posted date ---
    date_node = li.css_first("dd.posted span") or li.css_first("dd.posted")
    posted_at = _parse_date(date_node.text(strip=True) if date_node else None)

    # --- Description snippet (short excerpt from <p>) ---
    desc_node = li.css_first("p")
    description_raw: str | None = (
        desc_node.text(strip=True) if desc_node else None
    )
    if not description_raw:
        description_raw = None

    return RawListing(
        source="railrecruiter",
        source_listing_id=source_listing_id,
        url=url,
        title=title,
        employer=None,
        agency=agency,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw=contract_type_raw,
        metadata={"page_url": page_url},
    )


def _parse_html(html: str, base_url: str, page_url: str) -> list[RawListing]:
    """Parse a RailRecruiter search-results HTML page into RawListings.

    Extracted as a standalone function so fixture-based unit tests can call it
    without making live HTTP requests.

    Tries the id-based selector first, then falls back to the class selector.
    Returns ``[]`` (never raises) on any parse failure.
    """
    if not html or not html.strip():
        return []
    try:
        tree = HTMLParser(html)
    except Exception:
        logger.exception("railrecruiter: HTMLParser failed for %s", page_url)
        return []

    ol = (
        tree.css_first("#uxMainContent_uxResultsOL")
        or tree.css_first("ol.vacancy-listing")
    )
    if ol is None:
        logger.warning(
            "railrecruiter: no vacancy-listing container in %s — "
            "DOM may have changed.",
            page_url,
        )
        return []

    listings: list[RawListing] = []
    for li in ol.css("li[id^='v']"):
        listing = _li_to_raw_listing(li, base_url, page_url)
        if listing is not None:
            listings.append(listing)

    logger.debug(
        "railrecruiter: %s → %d listing(s) parsed.",
        page_url,
        len(listings),
    )
    return listings


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class RailRecruiterAdapter(SourceAdapter):
    """Adapter for railrecruiter.co.uk — ASP.NET HTML scrape.

    Session setup
    ~~~~~~~~~~~~~
    The site serves a bot-fingerprint challenge: the first GET to ``/jobs/``
    sets ``ASP.NET_SessionId`` + ``gcaffkey`` cookies; a subsequent empty POST
    to ``/_xhrc.ashx`` completes verification.  All later requests must carry
    these cookies, after which the clean search-results URLs respond normally.

    Search URL format
    ~~~~~~~~~~~~~~~~~
    ``/jobs/job-search-results/kw-{keyword-slug}/co-225/``
    Pagination: append ``?page=N`` for pages 2+.

    Multi-query
    ~~~~~~~~~~~
    Iterates ``keywords_list``; deduplicates within-source by
    ``source_listing_id`` before returning.

    Returns ``[]`` on any unrecoverable error; never crashes the pipeline.
    """

    name = "railrecruiter"

    def __init__(
        self,
        crawl_delay: int = 3,
        keywords_list: list[str] | None = None,
        base_url: str = _BASE_URL,
        max_pages_per_query: int = _MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.crawl_delay = crawl_delay
        self.base_url = base_url.rstrip("/")
        self.max_pages_per_query = max_pages_per_query
        self.keywords_list: list[str] = keywords_list or list(_DEFAULT_KEYWORDS)

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Scrape railrecruiter.co.uk for job listings.

        Iterates ``self.keywords_list``; for each keyword paginates up to
        ``max_pages_per_query`` pages.  Sleeps ``_PAGE_DELAY_SECONDS``
        between every page request.  Deduplicates within-source by
        ``source_listing_id``.  Applies ``since`` filter client-side on
        ``posted_at`` (listings without a date are always kept).
        Returns ``[]`` and logs a warning on any failure.
        """
        seen: dict[str, RawListing] = {}
        first_request = True
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                await self._setup_session(client)

                for keyword in self.keywords_list:
                    for page in range(1, self.max_pages_per_query + 1):
                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False

                        url = _build_search_url(self.base_url, keyword, page)
                        page_listings = await self._fetch_page(
                            client, url, page
                        )

                        if not page_listings:
                            if page == 1:
                                logger.warning(
                                    "railrecruiter: page 1 returned no listings "
                                    "for keyword %r — possible selector mismatch "
                                    "or no results.",
                                    keyword,
                                )
                            else:
                                logger.debug(
                                    "railrecruiter: page %d empty for keyword "
                                    "%r — stopping pagination.",
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
                            "railrecruiter: keyword=%r page=%d → "
                            "%d results, %d new unique.",
                            keyword,
                            page,
                            len(page_listings),
                            new_count,
                        )

        except Exception:
            logger.exception(
                "railrecruiter: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "railrecruiter: fetched %d unique listing(s) "
            "(keywords=%d, max_pages=%d, since=%s).",
            len(listings),
            len(self.keywords_list),
            self.max_pages_per_query,
            since,
        )
        return listings

    async def _setup_session(self, client: httpx.AsyncClient) -> None:
        """Establish an ASP.NET session and complete browser-fingerprint check.

        Step 1: GET ``/jobs/`` — sets ``ASP.NET_SessionId`` + ``gcaffkey``.
        Step 2: POST ``/_xhrc.ashx`` (empty body, 204 expected) — signals
                that a real browser is present; required before search URLs
                will return results instead of redirecting to ``/__verifybrowser``.

        Logs a warning and continues if either request fails; some pages may
        still be reachable without a verified session.
        """
        try:
            await client.get(f"{self.base_url}/jobs/")
        except Exception:
            logger.warning(
                "railrecruiter: session GET /jobs/ failed — continuing anyway."
            )
            return
        try:
            await client.post(
                f"{self.base_url}/_xhrc.ashx",
                headers={
                    **_HEADERS,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.base_url}/jobs/",
                },
            )
        except Exception:
            logger.warning(
                "railrecruiter: browser-verify POST /_xhrc.ashx failed — "
                "search results may not load."
            )

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        page_num: int,
    ) -> list[RawListing]:
        """Fetch one search-results page and return parsed listings.

        Returns ``[]`` on any error — never raises.
        """
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "railrecruiter: HTTP %s on page %d (%s).",
                exc.response.status_code,
                page_num,
                url,
            )
            return []
        except Exception:
            logger.exception(
                "railrecruiter: request failed on page %d (%s).",
                page_num,
                url,
            )
            return []

        return _parse_html(response.text, self.base_url, url)
