"""Totaljobs / CWJobs adapter — StepStone platform (parameterised).

One ``StepStoneAdapter`` instance is created per source config block in
config.toml.  Passing ``name``, ``domain``, ``search_path``, and
``crawl_delay`` at construction time lets the single class cover both
Totaljobs and CWJobs without branching.

Robots.txt compliance
---------------------
  Totaljobs : https://www.totaljobs.com/robots.txt  — allows /jobs/* paths;
              ``?page=N`` pagination is permitted.
  CWJobs    : https://www.cwjobs.co.uk/robots.txt   — mirrors Totaljobs.
  Crawl-delay: 3 s enforced between successive page fetches (config.toml).
  Page cap  : 5 pages per run (MVP politeness limit; well within robots allowance).

DOM selectors (StepStone platform, calibrated 2026-06-15)
----------------------------------------------------------
  Card root  : article[data-at="job-item"]
  Title/link : a[data-at="job-item-title"]
  Company    : [data-at="job-item-company-name"]
  Location   : [data-at="job-item-location"]
  Salary     : [data-at="job-item-salary-info"]   ← updated from "job-item-salary"
  Date       : [data-at="job-item-timeago"] > time  ← updated from "job-item-date"
  Contract   : not rendered in search cards — defaults to "contract"
  Snippet    : not rendered in search cards — left as None

  StepStone pages use Emotion CSS-in-JS: every element carries a sibling
  ``<style>`` block.  ``_STYLE_RE`` strips these before the HTML is parsed
  so that ``.text()`` calls on card nodes return clean content only.

  All selectors are attempted with graceful fallbacks.  If the primary
  selector returns zero results a WARNING is logged so Arthur's
  HTML-schema-change acceptance gate fires.

Job-ID extraction
-----------------
  Primary  : regex ``job(\\d+)`` from the listing URL suffix.
  Fallback : ``data-job-id`` / ``data-id`` card attributes.
  Last resort: raw URL used as opaque ID (avoids silently dropping listings).

Search URL (confirmed 2026-06-15)
----------------------------------
  Totaljobs : https://www.totaljobs.com/jobs/project-manager/in-uk?contract=true
  CWJobs    : https://www.cwjobs.co.uk/jobs/project-manager/in-uk?contract=true
  Both return 200 with 25 job cards per page.  The old
  ``/jobs/project-manager/engineering-jobs`` path returns HTTP 500.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.stepstone")

_REQUEST_TIMEOUT = 30.0
_MAX_PAGES = 5  # robots-courtesy cap; both sites allow /jobs/?page=N

# Strip Emotion CSS-in-JS <style> blocks embedded inside every rendered element.
# selectolax's .text() includes the CSS text, which pollutes title/employer fields.
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)

_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Matches "job<ID>" suffix in StepStone listing URLs (e.g. "-job123456789").
_JOB_ID_RE = re.compile(r"job(\d+)", re.IGNORECASE)

# Matches relative-date strings shown on listing cards.
_RELATIVE_DATE_RE = re.compile(
    r"(?P<n>\d+)\s*(?P<unit>second|minute|hour|day|week|month)s?\s*ago"
    r"|(?P<keyword>just\s*now|today|yesterday)",
    re.IGNORECASE,
)


def _extract_job_id(url: str) -> str | None:
    """Pull the numeric StepStone job ID from a listing URL."""
    m = _JOB_ID_RE.search(url)
    return m.group(1) if m else None


def _parse_relative_date(text: str) -> datetime | None:
    """Convert a relative-date string to a UTC-aware datetime.

    Handles: "just now", "today", "yesterday", "N seconds/minutes/hours/
    days/weeks/months ago".  Returns None if the text cannot be parsed.
    """
    if not text:
        return None
    m = _RELATIVE_DATE_RE.search(text.strip())
    if not m:
        return None

    now = datetime.now(timezone.utc)

    if m.group("keyword"):
        kw = m.group("keyword").lower().replace(" ", "")
        if kw in ("justnow", "today"):
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if kw == "yesterday":
            return (now - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        return None

    n = int(m.group("n"))
    unit = m.group("unit").lower()
    deltas: dict[str, timedelta] = {
        "second": timedelta(seconds=n),
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=n * 30),
    }
    delta = deltas.get(unit)
    return (now - delta) if delta is not None else None


def _node_text(node: Any) -> str | None:
    """Return stripped text from a selectolax node; None if absent or empty."""
    if node is None:
        return None
    text = node.text(strip=True)
    return text if text else None


class StepStoneAdapter(SourceAdapter):
    """Parameterised adapter for Totaljobs and CWJobs (StepStone platform).

    Instantiate once per source config block; ``name`` becomes the ``source``
    field on every ``RawListing`` (e.g. ``"totaljobs"`` or ``"cwjobs"``).
    """

    def __init__(
        self,
        name: str,
        domain: str,
        search_path: str,
        crawl_delay: int = 3,
    ) -> None:
        self.name = name
        self.domain = domain
        self.search_path = search_path
        self.crawl_delay = crawl_delay

    def _page_url(self, page: int) -> str:
        """Build the search-results URL for page ``page`` (1-indexed)."""
        base = f"https://{self.domain}{self.search_path}?contract=true"
        return base if page <= 1 else f"{base}&page={page}"

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch up to ``_MAX_PAGES`` pages, sleeping ``crawl_delay`` between them.

        Applies ``since`` client-side on parsed ``posted_at``.
        Returns ``[]`` and logs a warning on any unrecoverable error — never raises.
        """
        listings: list[RawListing] = []
        try:
            async with httpx.AsyncClient(
                headers=_BROWSER_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for page_num in range(1, _MAX_PAGES + 1):
                    if page_num > 1:
                        await asyncio.sleep(self.crawl_delay)

                    page_listings = await self._fetch_page(client, page_num)
                    if not page_listings:
                        # Empty page → no further results.
                        break

                    for listing in page_listings:
                        if since and listing.posted_at and listing.posted_at < since:
                            continue
                        listings.append(listing)

        except Exception:
            logger.exception(
                "%s fetch aborted — returning %d partial result(s).",
                self.name,
                len(listings),
            )
            return listings

        logger.info(
            "%s: fetched %d listing(s) (max_pages=%d, since=%s).",
            self.name,
            len(listings),
            _MAX_PAGES,
            since,
        )
        return listings

    async def _fetch_page(
        self, client: httpx.AsyncClient, page_num: int
    ) -> list[RawListing]:
        """Fetch and parse one results page; returns [] on HTTP or parse error."""
        url = self._page_url(page_num)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "%s HTTP %s on page %d (%s).",
                self.name,
                exc.response.status_code,
                page_num,
                url,
            )
            return []
        except Exception:
            logger.exception(
                "%s network error on page %d (%s).",
                self.name,
                page_num,
                url,
            )
            return []

        try:
            return self._parse_page(resp.text, page_num)
        except Exception:
            logger.exception(
                "%s parse error on page %d — DOM may have changed.",
                self.name,
                page_num,
            )
            return []

    def _parse_page(self, html: str, page_num: int) -> list[RawListing]:
        """Parse all job cards from a search-results HTML string.

        Strips Emotion CSS-in-JS ``<style>`` blocks first so that ``.text()``
        calls on card nodes return clean content rather than raw CSS.
        """
        clean_html = _STYLE_RE.sub("", html)
        tree = HTMLParser(clean_html)
        cards = tree.css('[data-at="job-item"]')
        if not cards:
            logger.warning(
                '%s: no [data-at="job-item"] cards on page %d '
                "(selector may have changed or page was blocked).",
                self.name,
                page_num,
            )
            return []

        listings: list[RawListing] = []
        for card in cards:
            try:
                listing = self._parse_card(card, page_num)
                if listing is not None:
                    listings.append(listing)
            except Exception:
                logger.warning(
                    "%s: skipping malformed card on page %d.",
                    self.name,
                    page_num,
                    exc_info=True,
                )
        return listings

    def _parse_card(self, card: Any, page_num: int) -> RawListing | None:
        """Map a single job-card node to a ``RawListing``; None on critical failure."""
        # --- Title + listing URL ---
        title_node = (
            card.css_first('a[data-at="job-item-title"]')
            or card.css_first('[data-at="job-item-title"] a')
            or card.css_first("h2 a")
            or card.css_first("h3 a")
        )
        if title_node is None:
            logger.debug(
                "%s: card on page %d has no title node — skipping.", self.name, page_num
            )
            return None

        title = title_node.text(strip=True)
        href: str = title_node.attributes.get("href", "")
        if href.startswith("/"):
            href = f"https://{self.domain}{href}"

        # --- Source listing ID ---
        source_id: str | None = _extract_job_id(href)
        if not source_id:
            source_id = card.attributes.get("data-job-id") or card.attributes.get(
                "data-id"
            )
        if not source_id:
            # Last resort: use the full URL as an opaque ID to avoid dropping listings.
            source_id = href or None

        if not source_id or not title:
            return None

        # --- Employer ---
        employer = _node_text(card.css_first('[data-at="job-item-company-name"]'))

        # --- Location ---
        location = _node_text(card.css_first('[data-at="job-item-location"]'))

        # --- Salary ---
        salary_raw = _node_text(card.css_first('[data-at="job-item-salary-info"]'))

        # --- Contract type ---
        # Employment-type tag is no longer rendered in StepStone search cards;
        # default to "contract" since we filter by contract=true in the URL.
        contract_raw = "contract"

        # --- Card snippet ---
        # Description is not rendered in search result cards on the current
        # StepStone platform.  Left as None; detail-fetch is deferred to v0.2.
        description: str | None = None

        # --- Posted date ---
        # StepStone now uses data-at="job-item-timeago" wrapping a <time> element
        # with relative text ("2 days ago", "1 week ago", etc.).
        date_node = card.css_first('[data-at="job-item-timeago"]') or card.css_first(
            "time"
        )
        date_text: str | None = None
        if date_node is not None:
            date_text = (
                date_node.attributes.get("datetime")
                or date_node.text(strip=True)
                or None
            )
        posted_at = _parse_relative_date(date_text) if date_text else None

        return RawListing(
            source=self.name,
            source_listing_id=str(source_id),
            url=href,
            title=title,
            employer=employer,
            location_raw=location,
            description_raw=description,
            posted_at=posted_at,
            salary_raw=salary_raw,
            contract_type_raw=contract_raw,
            metadata={
                "detail_fetched": False,
                "page_num": page_num,
            },
        )


if __name__ == "__main__":
    import asyncio as _asyncio

    from mechpm.config import Settings

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    _settings = Settings.load()
    _cfg = _settings.sources.get("totaljobs")
    _extra: dict[str, Any] = (_cfg.model_extra or {}) if _cfg else {}
    _domain = _extra.get("domain", "www.totaljobs.com")
    _search_path = _extra.get(
        "search_path", "/jobs/project-manager/in-uk"
    )
    _delay = _cfg.crawl_delay if _cfg else 3

    _adapter = StepStoneAdapter(
        name="totaljobs",
        domain=_domain,
        search_path=_search_path,
        crawl_delay=_delay,
    )
    _results = _asyncio.run(_adapter.fetch())
    print(f"Fetched {len(_results)} listing(s) from {_adapter.name}.")
    for _listing in _results[:2]:
        print(
            f"  [{_listing.source_listing_id}] '{_listing.title}'"
            f" | {_listing.employer}"
            f" | {_listing.location_raw}"
            f" | {_listing.salary_raw}"
        )
