"""Volcanic platform adapter — Navartis + Carrington West.

Overview
--------
Two UK specialist engineering recruitment sites share the Volcanic platform:

  * Navartis        (www.navartisglobal.com)   — rail / civil engineering
  * Carrington West (www.carringtonwest.com)   — infrastructure / highways / rail

Both sites serve job pages as plain HTML with no CAPTCHA or bot-management
blocking their static job pages. reCAPTCHA is only active on application forms.

Strategy
--------
1. Fetch ``{base_url}/job/sitemap.xml`` to enumerate all job URLs with
   ``<lastmod>`` dates.
2. Sort URLs by lastmod descending (most-recent first); cap at ``max_jobs``.
3. For each URL, fetch the HTML page and extract structured fields.
4. Filter results against ``keywords_list`` (case-insensitive substring match
   against title + description).
5. Apply ``since`` filter on ``posted_at``.
6. Deduplicate by URL.

HTML layouts (live recon 2026-06-22)
--------------------------------------
Both sites are Volcanic but use different themes, so extraction differs:

**Navartis** — JSON-LD ``JobPosting`` available on every page:
  - Title:        ``<h1 class="h2 m-0">…</h1>``
  - JSON-LD:      ``datePosted``, ``description`` (HTML), ``identifier.value``
                  (job ref), ``baseSalary``, ``jobLocation``, ``employmentType``
  - HTML details: ``<aside data-element="job-details">`` → ``<ul><li>`` items
                  with ``<strong>Label:</strong><span>Value</span>`` pairs.
                  Labels: Posted (relative), Location, Job Ref, Employment Type,
                  Salary, Sector, Contact.

**Carrington West** — HTML-only (no job JSON-LD):
  - Title:        ``<h1 class="job-title h1">…</h1>``
  - Details:      ``<div id="job-page">`` → ``<div class="job-details
                  flex-group"><dl class="flex-container">`` with
                  ``<dd class="flex-item *">`` elements.
  - Posted date:  ``dd.date-posted`` → plain text e.g. "19 June 2026"
  - Salary:       ``dd.job-salary`` → ``span.salary-free`` text
  - Location:     ``dd.job-location`` → text after label span
  - Job type:     ``dd.job-type`` → anchor text
  - Job ref:      ``dd.job-ref`` → text after label span
  - Description:  ``div.job-description.js-job-description``

Layout detection
----------------
Navartis pages have ``<aside data-element="job-details">``; CW pages do not.
The parser detects which layout is present and selects the matching extractor.

source_listing_id
-----------------
Extracted from the trailing numeric segment of the URL path::

    /job/commercial-assistant-pr-slash-172416  → "172416"
    /job/highways-officer-5941945              → "5941945"

Falls back to the full URL path slug when no trailing number is found.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.volcanic")

_SITEMAP_PATH = "/job/sitemap.xml"
_DEFAULT_MAX_JOBS = 200
_DEFAULT_CRAWL_DELAY = 3
_REQUEST_TIMEOUT = 30.0
_PAGE_DELAY_SECONDS = 1

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

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Trailing numeric ID in a Volcanic job URL slug, e.g. "job-title-5941945"
_RE_TRAILING_ID = re.compile(r"-(\d+)(?:\s*)$")

# Month name → integer map for parsing "DD Month YYYY" dates (Carrington West)
_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# Regex for "19 June 2026" or "1 Jan 2025" patterns
_RE_DATE_TEXT = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _extract_id_from_url(url: str) -> str:
    """Extract a stable listing ID from a Volcanic job URL.

    Prefers the trailing numeric segment from the URL path slug
    (e.g. ``"5941945"`` from ``"highways-officer-5941945"``).
    Falls back to the full URL path slug when no trailing number is found.

    Returns an empty string for empty or path-less inputs.

    Examples::

        >>> _extract_id_from_url(
        ...     "https://www.carringtonwest.com/job/highways-officer-5941945"
        ... )
        '5941945'
        >>> _extract_id_from_url(
        ...     "https://www.navartisglobal.com/job/commercial-assistant-pr-slash-172416"
        ... )
        '172416'
    """
    if not url:
        return ""
    path = url.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if "/" in path else path
    if not slug:
        return ""
    m = _RE_TRAILING_ID.search(slug)
    if m:
        return m.group(1)
    return slug


def _parse_sitemap_xml(xml_text: str) -> list[tuple[str, date | None]]:
    """Parse a Volcanic sitemap XML into (url, lastmod) pairs.

    Filters to URLs whose path contains ``/job/`` (excludes index pages).
    Sorts by lastmod descending (most-recent first); entries with no lastmod
    sort last.

    Returns ``[]`` on any parse error or empty input.
    """
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("volcanic: sitemap XML parse error: %s", exc)
        return []

    ns = {"sm": _SITEMAP_NS}
    entries: list[tuple[str, date | None]] = []
    for url_el in root.findall(".//sm:url", ns):
        loc_el = url_el.find("sm:loc", ns)
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        if "/job/" not in loc:
            continue

        lastmod: date | None = None
        lastmod_el = url_el.find("sm:lastmod", ns)
        if lastmod_el is not None and lastmod_el.text:
            try:
                lastmod = date.fromisoformat(lastmod_el.text.strip()[:10])
            except ValueError:
                pass

        entries.append((loc, lastmod))

    entries.sort(
        key=lambda x: x[1] if x[1] is not None else date.min,
        reverse=True,
    )
    return entries


def _parse_date_text(text: str | None) -> datetime | None:
    """Parse a human-readable date string to a UTC-aware datetime.

    Handles ``"DD Month YYYY"`` format used by Carrington West, e.g.
    ``"19 June 2026"``, ``"1 January 2025"``.  Uses a regex so surrounding
    text (e.g. ``"Posted19 June 2026"``) is handled gracefully.

    Returns ``None`` on any parse failure.
    """
    if not text:
        return None
    m = _RE_DATE_TEXT.search(text)
    if not m:
        return None
    day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
    month = _MONTH_MAP.get(month_s.lower())
    if not month:
        return None
    try:
        return datetime(int(year_s), month, int(day_s), tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_iso_datetime(text: str | None) -> datetime | None:
    """Parse an ISO 8601 date/datetime string to a UTC-aware datetime.

    Handles ``"YYYY-MM-DDTHH:MM:SS.sssZ"``, ``"YYYY-MM-DDTHH:MM:SS+HH:MM"``,
    and date-only ``"YYYY-MM-DD"`` formats.

    Returns ``None`` on failure.
    """
    if not text:
        return None
    s = text.strip()
    if not s:
        return None
    # Replace Z suffix for fromisoformat compatibility
    s_iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s_iso)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    # Try date-only (may have garbage after the date portion)
    date_part = s[:10]
    try:
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return None


def _matches_keywords(
    title: str,
    description: str | None,
    keywords_list: list[str],
) -> bool:
    """Return True if any keyword appears (case-insensitively) in title or description.

    When ``keywords_list`` is empty the filter is bypassed and ``True`` is
    always returned.
    """
    if not keywords_list:
        return True
    haystack = (title + " " + (description or "")).lower()
    return any(kw.lower() in haystack for kw in keywords_list)


def _build_salary_raw(jsonld: dict[str, Any]) -> str | None:
    """Build a human-readable salary string from a JSON-LD ``baseSalary`` block.

    Handles string, flat dict, and structured ``MonetaryAmount`` shapes.
    Returns ``None`` when absent or empty.
    """
    base_salary = jsonld.get("baseSalary")
    if not base_salary:
        return None
    if isinstance(base_salary, str):
        return base_salary.strip() or None
    if isinstance(base_salary, dict):
        currency = base_salary.get("currency", "")
        value = base_salary.get("value")
        if value is None:
            return None
        if isinstance(value, dict):
            min_val = value.get("minValue")
            max_val = value.get("maxValue")
            unit = value.get("unitText", "")
            if min_val is not None and max_val is not None:
                return f"{currency} {min_val:,}–{max_val:,} per {unit.lower()}".strip()
            if min_val is not None:
                return f"{currency} {min_val:,} per {unit.lower()}".strip()
        return f"{currency} {value}".strip() or None
    return None


# ---------------------------------------------------------------------------
# Field extractors (layout-specific)
# ---------------------------------------------------------------------------


def _extract_navartis_fields(tree: HTMLParser) -> dict[str, Any]:
    """Extract job fields from a Navartis-style Volcanic HTML page.

    Uses the JSON-LD ``JobPosting`` block (present on every Navartis job page)
    for ``datePosted``, ``description``, and ``identifier``.  Falls back to
    the ``<aside data-element="job-details">`` HTML for salary, location,
    contract type, and job ref (which provides the raw display strings).

    Returns a dict with keys:
      title, location_raw, salary_raw, contract_type_raw, posted_at,
      description_raw, source_listing_id.
    All values may be ``None``.
    """
    fields: dict[str, Any] = {
        "title": None,
        "location_raw": None,
        "salary_raw": None,
        "contract_type_raw": None,
        "posted_at": None,
        "description_raw": None,
        "source_listing_id": None,
    }

    # --- JSON-LD JobPosting ---
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text(strip=True)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "JobPosting":
            continue

        # Title from JSON-LD
        jld_title = (data.get("title") or "").strip()
        if jld_title:
            fields["title"] = jld_title

        # datePosted — JSON-LD has precise ISO timestamp; HTML only has "N days ago"
        fields["posted_at"] = _parse_iso_datetime(data.get("datePosted"))

        # description (full HTML string)
        desc = data.get("description")
        if desc and isinstance(desc, str):
            fields["description_raw"] = desc.strip() or None

        # baseSalary (fallback; HTML Salary span preferred below)
        if not fields["salary_raw"]:
            fields["salary_raw"] = _build_salary_raw(data)

        # jobLocation
        jl = data.get("jobLocation")
        loc_obj = jl[0] if isinstance(jl, list) and jl else jl
        if isinstance(loc_obj, dict):
            addr = loc_obj.get("address") or {}
            if isinstance(addr, dict):
                parts = [
                    v.strip()
                    for key in ("addressLocality", "addressRegion")
                    if (v := addr.get(key, "")) and v.strip()
                ]
                if parts:
                    fields["location_raw"] = ", ".join(parts)

        # identifier.value → source_listing_id
        ident = data.get("identifier")
        if isinstance(ident, dict):
            val = str(ident.get("value") or "").strip()
            if val:
                fields["source_listing_id"] = val

        # employmentType
        et = data.get("employmentType")
        if isinstance(et, list) and et:
            fields["contract_type_raw"] = ", ".join(str(x) for x in et) or None
        elif isinstance(et, str) and et.strip():
            fields["contract_type_raw"] = et.strip()

        break  # Only first JobPosting block used

    # --- HTML <aside data-element="job-details"> ---
    # HTML values override JSON-LD where available — they reflect the
    # recruiter's display strings which are often more informative.
    aside = tree.css_first('[data-element="job-details"]')
    if aside:
        for li in aside.css("li"):
            strong = li.css_first("strong")
            span = li.css_first("span")
            if strong is None or span is None:
                continue
            label = strong.text(strip=True).rstrip(":").strip().lower()
            value = span.text(strip=True)
            if not value:
                continue
            if label == "location":
                fields["location_raw"] = value
            elif label == "salary":
                # Prefer HTML salary string (human-readable display value)
                fields["salary_raw"] = value
            elif label in ("employment type",):
                fields["contract_type_raw"] = value
            elif label == "job ref":
                fields["source_listing_id"] = value

    # --- H1 title (most reliable fallback) ---
    h1 = tree.css_first("h1")
    if h1 and not fields["title"]:
        fields["title"] = h1.text(strip=True) or None

    return fields


def _extract_cw_fields(tree: HTMLParser) -> dict[str, Any]:
    """Extract job fields from a Carrington West-style Volcanic HTML page.

    Parses the ``<div id="job-page">`` section with ``dd.flex-item`` elements.
    No JSON-LD is available on CW job pages.

    Returns a dict with keys:
      title, location_raw, salary_raw, contract_type_raw, posted_at,
      description_raw, source_listing_id.
    All values may be ``None``.
    """
    fields: dict[str, Any] = {
        "title": None,
        "location_raw": None,
        "salary_raw": None,
        "contract_type_raw": None,
        "posted_at": None,
        "description_raw": None,
        "source_listing_id": None,
    }

    # --- H1 ---
    h1 = tree.css_first("h1.job-title")
    if h1 is None:
        h1 = tree.css_first("h1")
    if h1:
        fields["title"] = h1.text(strip=True) or None

    # --- Date posted ---
    date_dd = tree.css_first("dd.date-posted")
    if date_dd:
        # Full text may be "Posted19 June 2026" or "Posted 19 June 2026"
        full_text = date_dd.text(strip=True)
        fields["posted_at"] = _parse_date_text(full_text)

    # --- Salary ---
    salary_dd = tree.css_first("dd.job-salary")
    if salary_dd:
        # Prefer <span class="salary-free"> which has the clean value
        salary_span = salary_dd.css_first("span.salary-free")
        if salary_span is None:
            salary_span = salary_dd.css_first("span.salary")
        if salary_span:
            val = salary_span.text(strip=True)
        else:
            # Get text, stripping the "Salary" label span
            full = salary_dd.text(strip=True)
            label_span = salary_dd.css_first("span")
            if label_span:
                label = label_span.text(strip=True)
                val = full[len(label):].strip() if full.startswith(label) else full
            else:
                val = full
        fields["salary_raw"] = val or None

    # --- Location ---
    loc_dd = tree.css_first("dd.job-location")
    if loc_dd:
        full = loc_dd.text(strip=True)
        label_span = loc_dd.css_first("span")
        if label_span:
            label = label_span.text(strip=True)
            val = full[len(label):].strip() if full.startswith(label) else full
        else:
            val = full
        fields["location_raw"] = val or None

    # --- Contract / job type ---
    type_dd = tree.css_first("dd.job-type")
    if type_dd:
        # Prefer the anchor text (clean job type label)
        anchor = type_dd.css_first("a")
        if anchor:
            val = anchor.text(strip=True)
        else:
            full = type_dd.text(strip=True)
            label_span = type_dd.css_first("span")
            if label_span:
                label = label_span.text(strip=True)
                val = full[len(label):].strip() if full.startswith(label) else full
            else:
                val = full
        fields["contract_type_raw"] = val or None

    # --- Job reference ---
    ref_dd = tree.css_first("dd.job-ref")
    if ref_dd:
        full = ref_dd.text(strip=True)
        label_span = ref_dd.css_first("span")
        if label_span:
            label = label_span.text(strip=True)
            val = full[len(label):].strip() if full.startswith(label) else full
        else:
            val = full
        fields["source_listing_id"] = val or None

    # --- Description ---
    desc_div = tree.css_first("div.job-description")
    if desc_div:
        # Store the inner HTML (raw HTML string) so the extraction pipeline
        # can strip and clean it downstream.
        inner = desc_div.html
        if inner:
            # Remove outer tag wrapper to get inner HTML
            inner = re.sub(r"^<[^>]+>", "", inner, count=1)
            inner = re.sub(r"</[^>]+>$", "", inner, count=1)
            fields["description_raw"] = inner.strip() or None
        if not fields["description_raw"]:
            fields["description_raw"] = desc_div.text(strip=True) or None

    return fields


def _parse_job_html(
    html: str,
    url: str,
    source_name: str,
    employer_name: str,
) -> RawListing | None:
    """Parse a Volcanic job page and return a RawListing.

    Auto-detects layout:
    - Navartis style: ``<aside data-element="job-details">`` present
    - Carrington West style: ``<div id="job-page">`` present (or fallback)

    Returns ``None`` when HTML is empty or a title cannot be extracted.
    """
    if not html or not html.strip():
        return None

    try:
        tree = HTMLParser(html)
    except Exception:
        logger.exception("volcanic[%s]: HTMLParser failed for %s", source_name, url)
        return None

    # Layout detection: Navartis has the distinctive data-element aside
    if tree.css_first('[data-element="job-details"]') is not None:
        fields = _extract_navartis_fields(tree)
    else:
        fields = _extract_cw_fields(tree)

    title = (fields.get("title") or "").strip()
    if not title:
        logger.debug("volcanic[%s]: no title found at %s", source_name, url)
        return None

    # source_listing_id: prefer from HTML; fall back to URL trailing number/slug
    source_listing_id = (fields.get("source_listing_id") or "").strip()
    if not source_listing_id:
        source_listing_id = _extract_id_from_url(url)

    return RawListing(
        source=source_name,
        source_listing_id=source_listing_id,
        url=url,
        title=title,
        employer=employer_name,
        agency=None,
        location_raw=fields.get("location_raw"),
        description_raw=fields.get("description_raw"),
        posted_at=fields.get("posted_at"),
        salary_raw=fields.get("salary_raw"),
        contract_type_raw=fields.get("contract_type_raw"),
        metadata={},
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class VolcanicAdapter(SourceAdapter):
    """Adapter for the Volcanic job board platform.

    A single class handles all Volcanic-powered sites; only ``base_url`` and
    ``source_name`` differ between instances.

    Currently confirmed sites:

    * **Navartis** (``www.navartisglobal.com``) — rail/civil engineering recruiter
    * **Carrington West** (``www.carringtonwest.com``) — highways/infrastructure/rail

    Both sites expose a sitemap at ``/job/sitemap.xml``.  Individual job pages
    are plain HTML with no CAPTCHA or bot-management on GET requests.

    Args:
        base_url:      Base URL of the site, e.g. ``"https://www.navartisglobal.com"``.
        source_name:   Logical name used in ``RawListing.source``.
        crawl_delay:   Seconds the orchestrator sleeps between adapters (default 3).
        keywords_list: Keywords to filter against title/description.
                       Empty list = return all results.
        max_jobs:      Maximum number of job pages to fetch per run (default 200).
                       Sitemap URLs are sorted by ``lastmod`` descending so the
                       most-recent postings are always fetched first.
    """

    def __init__(
        self,
        base_url: str,
        source_name: str,
        crawl_delay: int = _DEFAULT_CRAWL_DELAY,
        keywords_list: list[str] | None = None,
        max_jobs: int = _DEFAULT_MAX_JOBS,
        **kwargs: object,
    ) -> None:
        self.name = source_name
        self.base_url = base_url.rstrip("/")
        self.crawl_delay = crawl_delay
        self.keywords_list: list[str] = keywords_list or []
        self.max_jobs = max_jobs
        # employer_name used in RawListing.employer — convert source_name to
        # a display-friendly string, e.g. "navartis" → "Navartis",
        # "carrington_west" → "Carrington West"
        self.employer_name: str = source_name.replace("_", " ").title()

    def _sitemap_url(self) -> str:
        return f"{self.base_url}{_SITEMAP_PATH}"

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch Volcanic job listings for this site.

        1. Fetches sitemap; sorts by lastmod descending; caps at ``max_jobs``.
        2. Fetches each job page; applies keyword and since filters.
        3. Deduplicates by URL before returning.
        4. Returns ``[]`` and logs a warning on any unrecoverable error.
        """
        results: list[RawListing] = []
        seen_urls: set[str] = set()

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                entries = await self._fetch_sitemap(client)
                if not entries:
                    logger.warning(
                        "volcanic[%s]: no URLs in sitemap — nothing to fetch.",
                        self.name,
                    )
                    return []

                # Cap to max_jobs most-recent
                entries = entries[: self.max_jobs]
                logger.info(
                    "volcanic[%s]: %d job URL(s) to fetch (max_jobs=%d).",
                    self.name,
                    len(entries),
                    self.max_jobs,
                )

                for i, (job_url, _lastmod) in enumerate(entries):
                    if i > 0:
                        await asyncio.sleep(_PAGE_DELAY_SECONDS)

                    if job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)

                    listing = await self._fetch_job(client, job_url)
                    if listing is None:
                        continue

                    # Keyword filter
                    if not _matches_keywords(
                        listing.title,
                        listing.description_raw,
                        self.keywords_list,
                    ):
                        logger.debug(
                            "volcanic[%s]: no keyword match — skipping %s",
                            self.name,
                            job_url,
                        )
                        continue

                    # Since filter
                    if since and listing.posted_at and listing.posted_at < since:
                        continue

                    results.append(listing)

        except Exception:
            logger.exception(
                "volcanic[%s]: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                self.name,
                len(results),
            )
            return results

        logger.info(
            "volcanic[%s]: %d matching listing(s) fetched "
            "(max_jobs=%d, keywords=%d, since=%s).",
            self.name,
            len(results),
            self.max_jobs,
            len(self.keywords_list),
            since,
        )
        return results

    async def _fetch_sitemap(
        self,
        client: httpx.AsyncClient,
    ) -> list[tuple[str, date | None]]:
        """Fetch and parse the sitemap XML. Returns [] on any failure."""
        url = self._sitemap_url()
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning(
                "volcanic[%s]: request error fetching sitemap %s: %s",
                self.name,
                url,
                exc,
            )
            return []
        if response.status_code != 200:
            logger.warning(
                "volcanic[%s]: HTTP %d fetching sitemap %s",
                self.name,
                response.status_code,
                url,
            )
            return []
        return _parse_sitemap_xml(response.text)

    async def _fetch_job(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> RawListing | None:
        """Fetch one job page and parse it. Returns None on any error."""
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            logger.warning(
                "volcanic[%s]: request error fetching %s: %s",
                self.name,
                url,
                exc,
            )
            return None
        if response.status_code != 200:
            logger.warning(
                "volcanic[%s]: HTTP %d for %s",
                self.name,
                response.status_code,
                url,
            )
            return None
        return _parse_job_html(
            response.text,
            url,
            self.name,
            self.employer_name,
        )
