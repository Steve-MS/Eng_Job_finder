"""The Engineer Jobs adapter — Mark Allen Group / Cloudflare two-phase strategy.

Phase A (default): httpx + realistic browser headers. No JS rendering required.
Phase B (fallback): Playwright headless Chromium, triggered only when Phase A
    encounters a Cloudflare challenge page (HTTP 403/503 with CF markers, or a
    silently-tunnelled CF challenge inside a 200 body).

robots.txt: User-agent: * Allow: / — crawling is explicitly permitted.
Crawl-delay: 5 s between page requests (intra-source).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.the_engineer")

_SEARCH_URL = "https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract"
_PAGE_DELAY_SECONDS = 5
_MAX_PAGES = 5
_REQUEST_TIMEOUT = 30.0

# Cloudflare challenge fingerprints — presence in body signals a block page.
_CF_CHALLENGE_MARKERS = (
    "Just a moment...",
    "cf-browser-verification",
    "_cf_chl_",
    "Attention Required! | Cloudflare",
)

# Realistic Chrome 124 on Windows — matches the browser profile Cloudflare expects.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://jobs.theengineer.co.uk/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}

# Mark Allen Group card selectors — tried in order, first non-empty match wins.
_CARD_SELECTORS = [
    "article.job",
    "[data-job-id]",
    ".job-card",
    "li.job",
    ".job-listing",
    ".result-item",
    ".vacancy",
]

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d %B %Y",
    "%d %b %Y",
)


class CloudflareChallengeError(Exception):
    """Raised by Phase A when a Cloudflare JS challenge or block page is detected."""


class TheEngineerAdapter(SourceAdapter):
    """Adapter for jobs.theengineer.co.uk — Mark Allen Group / Cloudflare.

    Phase A: httpx with browser-like headers.  Returns listings on success.
    Phase B: Playwright headless Chromium fallback (optional dep: mechpm[browser]).
             Triggered only when Phase A raises CloudflareChallengeError or
             returns the None sentinel.
    """

    name = "the_engineer"
    crawl_delay: int

    def __init__(self, crawl_delay: int = 5, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch contract PM listings from The Engineer Jobs.

        Tries Phase A (httpx) first.  Falls through to Phase B (Playwright)
        only if Phase A signals a Cloudflare block or returns None.
        Never raises — returns [] on any unrecoverable failure.
        """
        result: list[RawListing] | None = None

        # --- Phase A ---
        try:
            result = await self._fetch_httpx()
        except CloudflareChallengeError as exc:
            logger.warning(
                "the_engineer: Phase A blocked by Cloudflare challenge (%s) — "
                "falling through to Phase B (Playwright).",
                exc,
            )
        except Exception:
            logger.exception(
                "the_engineer: Phase A raised an unexpected error — "
                "falling through to Phase B (Playwright)."
            )

        if result is not None:
            logger.info(
                "the_engineer: Phase A (httpx) succeeded — %d listing(s).",
                len(result),
            )
            if since:
                result = _apply_since_filter(result, since)
            return result

        # --- Phase B ---
        try:
            listings = await self._fetch_playwright()
            logger.info(
                "the_engineer: Phase B (Playwright) succeeded — %d listing(s).",
                len(listings),
            )
            if since:
                listings = _apply_since_filter(listings, since)
            return listings
        except ImportError:
            logger.warning(
                "the_engineer: Playwright is not installed. "
                "To enable Phase B, run: "
                "pip install mechpm[browser] && playwright install chromium. "
                "Returning [].",
                extra={
                    "phase": "B",
                    "fix": "pip install mechpm[browser] && playwright install chromium",
                },
            )
            return []
        except Exception:
            logger.exception(
                "the_engineer: Phase B (Playwright) raised an unexpected error — returning []."
            )
            return []

    # ------------------------------------------------------------------
    # Phase A — httpx
    # ------------------------------------------------------------------

    async def _fetch_httpx(self) -> list[RawListing] | None:
        """Fetch listings via httpx with realistic browser headers.

        Returns:
            list[RawListing]: Parsed listings (may be empty — not a Phase B trigger).
            None: Explicit Phase B trigger (not currently returned; use
                CloudflareChallengeError instead, but callers handle None too).

        Raises:
            CloudflareChallengeError: On any Cloudflare JS challenge or block page.
        """
        listings: list[RawListing] = []

        async with httpx.AsyncClient(
            headers=_BROWSER_HEADERS,
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            for page_num in range(1, _MAX_PAGES + 1):
                url = (
                    _SEARCH_URL
                    if page_num == 1
                    else f"{_SEARCH_URL}&page={page_num}"
                )

                try:
                    response = await client.get(url)
                except Exception:
                    logger.exception(
                        "the_engineer: httpx network error on page %d — stopping pagination.",
                        page_num,
                    )
                    break

                body = response.text

                # CF challenge can appear on any status code (including 200 tunnelling).
                if _is_cf_challenge(response.status_code, body):
                    raise CloudflareChallengeError(
                        f"Cloudflare challenge on page {page_num} "
                        f"(HTTP {response.status_code})"
                    )

                if response.status_code not in (200,):
                    logger.warning(
                        "the_engineer: HTTP %d on page %d — stopping pagination.",
                        response.status_code,
                        page_num,
                    )
                    break

                page_listings = _parse_listings(body, fetch_phase="httpx")
                logger.debug(
                    "the_engineer: page %d → %d listing(s) (httpx).",
                    page_num,
                    len(page_listings),
                )
                listings.extend(page_listings)

                if not page_listings:
                    # Past the last results page — no need to continue.
                    break

                if page_num < _MAX_PAGES:
                    await asyncio.sleep(_PAGE_DELAY_SECONDS)

        return listings

    # ------------------------------------------------------------------
    # Phase B — Playwright (lazy import)
    # ------------------------------------------------------------------

    async def _fetch_playwright(self) -> list[RawListing]:
        """Fetch listings via headless Playwright Chromium.

        The playwright import is lazy so the adapter loads cleanly even when
        the mechpm[browser] extra is not installed.

        Raises:
            ImportError: re-raised if playwright is not installed
                (caller in fetch() handles this and warns the user).
        """
        from playwright.async_api import async_playwright  # noqa: PLC0415  # lazy

        listings: list[RawListing] = []
        pw_ctx = None
        browser = None

        try:
            pw_ctx = await async_playwright().start()
            browser = await pw_ctx.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=_BROWSER_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 900},
                extra_http_headers={
                    k: v for k, v in _BROWSER_HEADERS.items() if k != "User-Agent"
                },
            )
            page = await context.new_page()

            for page_num in range(1, _MAX_PAGES + 1):
                url = (
                    _SEARCH_URL
                    if page_num == 1
                    else f"{_SEARCH_URL}&page={page_num}"
                )

                try:
                    await page.goto(url, wait_until="networkidle", timeout=60_000)
                except Exception:
                    # networkidle timed out; try waiting for a listing card directly.
                    try:
                        await page.wait_for_selector(
                            ", ".join(_CARD_SELECTORS), timeout=15_000
                        )
                    except Exception:
                        logger.warning(
                            "the_engineer: Playwright page %d load/selector wait "
                            "timed out — stopping pagination.",
                            page_num,
                        )
                        break

                content = await page.content()
                page_listings = _parse_listings(content, fetch_phase="playwright")
                logger.debug(
                    "the_engineer: page %d → %d listing(s) (playwright).",
                    page_num,
                    len(page_listings),
                )
                listings.extend(page_listings)

                if not page_listings:
                    break

                if page_num < _MAX_PAGES:
                    await asyncio.sleep(_PAGE_DELAY_SECONDS)

        except ImportError:
            raise  # let fetch() catch and warn user cleanly
        except Exception:
            logger.exception(
                "the_engineer: Playwright fetch error — returning %d listing(s) "
                "collected before the failure.",
                len(listings),
            )
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    logger.debug("the_engineer: error closing Playwright browser.", exc_info=True)
            if pw_ctx is not None:
                try:
                    await pw_ctx.stop()
                except Exception:
                    logger.debug("the_engineer: error stopping Playwright context.", exc_info=True)

        return listings


# ------------------------------------------------------------------
# Shared HTML parsing helpers
# ------------------------------------------------------------------

def _is_cf_challenge(status_code: int, body: str) -> bool:
    """Return True if the response looks like a Cloudflare challenge page."""
    if status_code in (403, 503):
        return any(marker in body for marker in _CF_CHALLENGE_MARKERS)
    # Also catch CF challenges tunnelled inside a 200 response.
    return any(marker in body for marker in _CF_CHALLENGE_MARKERS)


def _parse_listings(html: str, *, fetch_phase: str) -> list[RawListing]:
    """Parse job-card nodes from raw HTML into RawListing objects.

    Tries each selector in _CARD_SELECTORS in order; uses the first
    non-empty result set.  Individual card parse failures are swallowed
    to keep the pipeline alive.
    """
    tree = HTMLParser(html)
    cards = _find_cards(tree)
    if not cards:
        logger.debug(
            "the_engineer: no listing cards found with any known selector "
            "(phase=%s).",
            fetch_phase,
        )
        return []

    listings: list[RawListing] = []
    for card in cards:
        try:
            listing = _map_card(card, fetch_phase=fetch_phase)
            if listing is not None:
                listings.append(listing)
        except Exception:
            logger.debug(
                "the_engineer: failed to parse a card node — skipping.", exc_info=True
            )
    return listings


def _find_cards(tree: HTMLParser):
    """Return node list for the first selector in _CARD_SELECTORS that matches."""
    for selector in _CARD_SELECTORS:
        nodes = tree.css(selector)
        if nodes:
            return nodes
    return []


def _map_card(card, *, fetch_phase: str) -> RawListing | None:
    """Map a selectolax Node to a RawListing, tolerating missing fields gracefully."""
    # URL + listing ID ------------------------------------------------
    link_node = (
        card.css_first("a[href]")
        or card.css_first("h2 a")
        or card.css_first("h3 a")
    )
    url = ""
    if link_node:
        href = link_node.attributes.get("href", "")
        url = (
            href
            if href.startswith("http")
            else urljoin("https://jobs.theengineer.co.uk", href)
        )

    listing_id = card.attributes.get("data-job-id", "")
    if not listing_id:
        listing_id = _id_from_url(url)
    if not listing_id:
        # No identifiable ID — likely UI chrome, skip.
        return None

    # Title -----------------------------------------------------------
    title = (
        _text(card.css_first(".job-title"))
        or _text(card.css_first("h2"))
        or _text(card.css_first("h3"))
        or _text(card.css_first(".title"))
        or ""
    )

    # Employer --------------------------------------------------------
    employer = (
        _text(card.css_first(".company"))
        or _text(card.css_first(".employer"))
        or _text(card.css_first(".recruiter"))
        or _text(card.css_first(".company-name"))
        or None
    )

    # Location --------------------------------------------------------
    location_raw = (
        _text(card.css_first(".location"))
        or _text(card.css_first(".job-location"))
        or _text(card.css_first("[data-location]"))
        or None
    )

    # Salary ----------------------------------------------------------
    salary_raw = (
        _text(card.css_first(".salary"))
        or _text(card.css_first(".job-salary"))
        or _text(card.css_first(".compensation"))
        or None
    )

    # Posted date -----------------------------------------------------
    date_node = (
        card.css_first("time")
        or card.css_first(".date")
        or card.css_first(".posted")
        or card.css_first(".job-date")
    )
    posted_at: datetime | None = None
    if date_node:
        raw_date = date_node.attributes.get("datetime") or _text(date_node) or ""
        posted_at = _parse_date(raw_date)

    return RawListing(
        source="the_engineer",
        source_listing_id=listing_id,
        url=url,
        title=title,
        employer=employer,
        agency=None,
        location_raw=location_raw,
        description_raw=None,  # not exposed on listing cards
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw="contract",
        metadata={"fetch_phase": fetch_phase},
    )


# ------------------------------------------------------------------
# Utility helpers
# ------------------------------------------------------------------

def _text(node) -> str | None:
    """Return stripped inner text of a selectolax node, or None if absent/empty."""
    if node is None:
        return None
    t = node.text(strip=True)
    return t if t else None


def _id_from_url(url: str) -> str:
    """Derive a listing identifier from the last non-empty URL path segment."""
    if not url:
        return ""
    parts = [p for p in url.rstrip("/").split("/") if p]
    return parts[-1] if parts else ""


def _parse_date(date_str: str) -> datetime | None:
    """Parse a date string to a UTC-aware datetime using common formats."""
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.debug("the_engineer: could not parse date string: %r", date_str)
    return None


def _apply_since_filter(
    listings: list[RawListing], since: datetime
) -> list[RawListing]:
    """Client-side filter: drop listings posted before ``since`` (when known)."""
    return [
        r for r in listings if r.posted_at is None or r.posted_at >= since
    ]


# ------------------------------------------------------------------
# Self-test  (python -m mechpm.adapters.the_engineer)
# ------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio as _asyncio

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    _adapter = TheEngineerAdapter()
    _results = _asyncio.run(_adapter.fetch())

    if not _results:
        print("No listings returned (check logs for detail).")
    else:
        _phases = {r.metadata.get("fetch_phase") for r in _results}
        print(f"Fetched {len(_results)} listing(s).")
        print(f"Phase(s) used: {', '.join(sorted(str(p) for p in _phases))}")
        _first = _results[0]
        print(
            f"  Sample: {_first.title!r} | employer={_first.employer} "
            f"| location={_first.location_raw} | salary={_first.salary_raw}"
        )
