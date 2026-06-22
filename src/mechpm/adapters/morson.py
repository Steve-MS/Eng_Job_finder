"""Morson Talent adapter — Drupal JSON:API for morson.com.

Overview
--------
Morson Talent (morson.com) is a major UK engineering recruitment agency
running a Drupal CMS with JSON:API enabled.

Discovery (live recon 2026-06-22)
----------------------------------
- Endpoint: GET https://www.morson.com/jsonapi/lm/job?sort=-created&page[limit]=50
- Accept: application/vnd.api+json
- No bot protection observed; no authentication required
- Keyword filter confirmed working:
    filter[title][operator]=CONTAINS&filter[title][value]={keyword}
- Pagination via links.next (offset-based URLs)
- No meta.total in response

Confirmed field map (attributes object, 2026-06-22)
-----------------------------------------------------
  drupal_internal__nid          → source_listing_id (int → str)
  title                         → title
  path.alias                    → url path (prefix with api_base)
  created                       → posted_at (ISO 8601 with TZ offset)
  field_c_j_description.value   → description_raw (HTML string)
  field_location                → location_raw (plain string, e.g. "London, UK")
  field_c_j_salary_text         → salary_raw (preferred, e.g. "$75 - 82 per year")
  field_c_j_salary_from         → fallback salary lower bound
  field_c_j_salary_to           → fallback salary upper bound
  field_c_j_salary_per          → fallback period code: A=annual, H=hourly, D=daily
  field_c_j_salary_currency     → fallback currency code (e.g. "GBP", "CAD")
  field_c_j_work_hours          → contract_type_raw (may be None)
  field_c_j_lm_reference        → metadata["lm_reference"] (e.g. "JOB-31436")
  field_c_j_consultant          → metadata["consultant"]
  field_c_j_remote              → metadata["remote"] (bool)
  unpublish_on                  → metadata["unpublish_on"]

Keyword filtering
-----------------
JSON:API server-side filtering via filter[title][operator]=CONTAINS is used
as the primary fetch strategy — one paginated request chain per keyword.

Pagination
----------
Follows links.next URL until exhausted or max_pages per keyword is reached.
Deduplicates across all keyword/page fetches by drupal_internal__nid.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.morson")

_SOURCE_NAME = "morson"
_DEFAULT_API_BASE = "https://www.morson.com"
_JSONAPI_PATH = "/jsonapi/lm/job"
_EMPLOYER = "Morson Group"
_PAGE_LIMIT = 50
_MAX_PAGES = 10
_PAGE_DELAY_SECONDS = 3
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
    "Accept": "application/vnd.api+json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

# Drupal salary period codes → human-readable label
_SALARY_PER_LABELS: dict[str, str] = {
    "A": "per year",
    "H": "per hour",
    "D": "per day",
    "W": "per week",
    "M": "per month",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_api_url(
    api_base: str,
    keyword: str | None = None,
    limit: int = _PAGE_LIMIT,
) -> str:
    """Build a Morson JSON:API URL for one keyword.

    When ``keyword`` is None, fetches all jobs without title filtering.

    Examples::

        >>> _build_api_url("https://www.morson.com", "project manager")
        'https://www.morson.com/jsonapi/lm/job?sort=-created&page%5Blimit%5D=50&filter%5Btitle%5D%5Boperator%5D=CONTAINS&filter%5Btitle%5D%5Bvalue%5D=project+manager'
        >>> _build_api_url("https://www.morson.com")
        'https://www.morson.com/jsonapi/lm/job?sort=-created&page%5Blimit%5D=50'
    """
    params: dict[str, str] = {
        "sort": "-created",
        "page[limit]": str(limit),
    }
    if keyword:
        params["filter[title][operator]"] = "CONTAINS"
        params["filter[title][value]"] = keyword
    return f"{api_base.rstrip('/')}{_JSONAPI_PATH}?{urlencode(params)}"


def _parse_salary(attrs: dict[str, Any]) -> str | None:
    """Build a human-readable salary string from JSON:API salary attributes.

    Prefers ``field_c_j_salary_text`` when non-empty.  Falls back to
    constructing a string from the numeric from/to/per/currency fields.

    Returns None when no salary information is available.
    """
    text = attrs.get("field_c_j_salary_text")
    if text and str(text).strip():
        return str(text).strip()

    salary_from = attrs.get("field_c_j_salary_from")
    salary_to = attrs.get("field_c_j_salary_to")
    per_code = attrs.get("field_c_j_salary_per")
    currency = attrs.get("field_c_j_salary_currency") or ""

    if not salary_from and not salary_to:
        return None

    per_label = ""
    if per_code:
        per_label = _SALARY_PER_LABELS.get(str(per_code).upper(), "")
    currency_prefix = f"{currency} " if currency else ""

    parts: list[str] = []
    if salary_from and salary_to:
        parts.append(f"{currency_prefix}{salary_from} - {salary_to}")
    elif salary_from:
        parts.append(f"{currency_prefix}{salary_from}")
    else:
        parts.append(f"{currency_prefix}{salary_to}")

    if per_label:
        parts.append(per_label)

    return " ".join(parts).strip() or None


def _parse_description(desc_field: Any) -> str | None:
    """Extract the HTML description string from a Drupal text field.

    The field may be:
    - A dict with a ``value`` key (standard Drupal ``text_with_summary`` format).
    - A plain string (defensive fallback).
    - None or an empty dict.

    Returns None when no description content is available.
    """
    if desc_field is None:
        return None
    if isinstance(desc_field, dict):
        value = desc_field.get("value")
        if value and str(value).strip():
            return str(value).strip()
        return None
    if isinstance(desc_field, str) and desc_field.strip():
        return desc_field.strip()
    return None


def _item_to_raw_listing(item: dict[str, Any], api_base: str) -> RawListing | None:
    """Map one JSON:API ``node--job`` data item to a ``RawListing``.

    Returns None if the item lacks a title or a stable identifier.
    """
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes")
    if not isinstance(attrs, dict) or not attrs:
        return None

    title = (attrs.get("title") or "").strip()
    if not title:
        return None

    nid = attrs.get("drupal_internal__nid")
    source_id = str(nid) if nid is not None else (item.get("id") or "").strip()
    if not source_id:
        return None

    # URL: prefer path.alias; fall back to a constructed path
    path_obj = attrs.get("path")
    path_alias = ""
    if isinstance(path_obj, dict):
        path_alias = (path_obj.get("alias") or "").strip()
    url = (
        f"{api_base.rstrip('/')}{path_alias}"
        if path_alias
        else f"{api_base.rstrip('/')}/jobs/{source_id}"
    )

    # posted_at: created is ISO 8601 with TZ offset
    posted_at: datetime | None = None
    created_str = attrs.get("created")
    if created_str:
        try:
            posted_at = datetime.fromisoformat(str(created_str))
        except ValueError:
            logger.debug("morson: could not parse created=%r for nid=%s", created_str, source_id)

    description_raw = _parse_description(attrs.get("field_c_j_description"))
    location_raw = attrs.get("field_location") or None
    salary_raw = _parse_salary(attrs)
    contract_type_raw = attrs.get("field_c_j_work_hours") or None

    metadata: dict[str, Any] = {}
    if lm_ref := attrs.get("field_c_j_lm_reference"):
        metadata["lm_reference"] = lm_ref
    if consultant := attrs.get("field_c_j_consultant"):
        metadata["consultant"] = consultant
    remote = attrs.get("field_c_j_remote")
    if remote is not None:
        metadata["remote"] = remote
    if currency := attrs.get("field_c_j_salary_currency"):
        metadata["salary_currency"] = currency
    if unpublish := attrs.get("unpublish_on"):
        metadata["unpublish_on"] = unpublish

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=source_id,
        url=url,
        title=title,
        employer=_EMPLOYER,
        location_raw=location_raw,
        description_raw=description_raw,
        posted_at=posted_at,
        salary_raw=salary_raw,
        contract_type_raw=contract_type_raw,
        metadata=metadata,
    )


def _parse_page(
    payload: dict[str, Any], api_base: str
) -> tuple[list[RawListing], str | None]:
    """Parse a JSON:API response page into ``(listings, next_url)``.

    ``next_url`` is the href from ``links.next`` if present, otherwise None.
    Returns ``([], None)`` on any parse error — never raises.
    """
    try:
        data = payload.get("data", [])
        if not isinstance(data, list):
            return [], None

        listings: list[RawListing] = []
        for item in data:
            listing = _item_to_raw_listing(item, api_base)
            if listing is not None:
                listings.append(listing)

        next_url: str | None = None
        links = payload.get("links", {})
        if isinstance(links, dict):
            next_link = links.get("next")
            if isinstance(next_link, dict):
                next_url = next_link.get("href")
            elif isinstance(next_link, str):
                next_url = next_link

        return listings, next_url
    except Exception:
        logger.exception("morson: error parsing API response page")
        return [], None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class MorsonAdapter(SourceAdapter):
    """Adapter for Morson Talent — Drupal JSON:API at morson.com.

    Fetches jobs via the ``/jsonapi/lm/job`` endpoint with server-side
    title filtering per keyword.  Paginates via ``links.next`` up to
    ``max_pages`` per keyword.  Deduplicates by ``drupal_internal__nid``
    across all keyword/page fetches.  Applies the ``since`` filter
    client-side on ``posted_at``.  Returns ``[]`` and logs a warning on
    any failure — never raises from ``fetch()``.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        api_base: str = _DEFAULT_API_BASE,
        crawl_delay: int = _PAGE_DELAY_SECONDS,
        keywords_list: list[str] | None = None,
        max_pages: int = _MAX_PAGES,
        **kwargs: object,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.crawl_delay = crawl_delay
        self.keywords_list: list[str] = keywords_list or list(_DEFAULT_KEYWORDS)
        self.max_pages = max_pages

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch listings from Morson Talent's JSON:API.

        For each keyword in ``keywords_list``, issues a title-filtered
        JSON:API request and paginates via ``links.next`` up to
        ``max_pages``.  Sleeps ``_PAGE_DELAY_SECONDS`` between every
        request.  Deduplicates across keywords by ``source_listing_id``.
        Applies ``since`` filter client-side on ``posted_at`` (listings
        with no date are always kept).  Returns ``[]`` and logs a warning
        on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}
        first_request = True

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for keyword in self.keywords_list:
                    next_url: str | None = _build_api_url(self.api_base, keyword)
                    page_num = 0

                    while next_url and page_num < self.max_pages:
                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False
                        page_num += 1

                        page_listings, next_url = await self._fetch_page(
                            client, next_url, page_num, keyword
                        )

                        if not page_listings:
                            logger.debug(
                                "morson: page %d empty for keyword %r — stopping.",
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
                            "morson: keyword=%r page=%d → %d results, %d new unique.",
                            keyword,
                            page_num,
                            len(page_listings),
                            new_count,
                        )

        except Exception:
            logger.exception(
                "morson: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "morson: fetched %d unique listing(s) "
            "(keywords=%d, max_pages=%d, since=%s).",
            len(listings),
            len(self.keywords_list),
            self.max_pages,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        page_num: int,
        keyword: str,
    ) -> tuple[list[RawListing], str | None]:
        """Fetch one JSON:API page and return ``(listings, next_url)``.

        Returns ``([], None)`` on any error — never raises.
        """
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "morson: HTTP %s on page %d for keyword %r (%s).",
                exc.response.status_code,
                page_num,
                keyword,
                url,
            )
            return [], None
        except Exception:
            logger.exception(
                "morson: request failed on page %d for keyword %r (%s).",
                page_num,
                keyword,
                url,
            )
            return [], None

        try:
            payload = response.json()
        except Exception:
            logger.warning(
                "morson: JSON decode failed for page %d keyword %r.",
                page_num,
                keyword,
            )
            return [], None

        return _parse_page(payload, self.api_base)
