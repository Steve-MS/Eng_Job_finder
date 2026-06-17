"""Turner & Townsend careers adapter — SmartRecruiters-backed JSON API.

Overview
--------
T&T proxies SmartRecruiters via a custom API endpoint at their careers site.
No auth required; robots.txt is permissive (only /umbraco/ and auth paths blocked).

Discovery (coordinator recon 2026-06-18)
-----------------------------------------
- POST https://www.turnerandtownsend.com/api/careers/searchvacancies
- Content-Type: application/json
- Referer: https://www.turnerandtownsend.com/join-us/current-opportunities/
- Payload: {"page": 1, "pageSize": 50, "query": "...", "countries": ["United Kingdom"]}
- Response: listingModel.content[] + paginationModel.totalPages
- Each listing's ``ref`` field is a public SmartRecruiters API URL that can be
  GET-fetched (Accept: application/json) for full job detail.

Field map (listingModel.content[])
-----------------------------------
  name                               → title
  ref                                → SmartRecruiters API URL;
                                         extract numeric ID for source_listing_id
  releasedDate                       → posted_at  (ISO-8601)
  location.city + location.country   → location_raw
  department.label                   → metadata.department
  customField[Discipline].valueLabel → metadata.discipline

Optional enrichment (GET ref URL, Accept: application/json)
------------------------------------------------------------
  typeOfEmployment.label                    → contract_type_raw
  jobAd.sections.jobDescription.text        → description_raw  (HTML)
  postingUrl                                → url (human-friendly page)
  experienceLevel.label                     → metadata.experience_level

Pagination strategy
-------------------
  For each keyword, POST pages 1..N until paginationModel.totalPages is consumed
  or max_pages_per_query is reached.
  Union-dedup by source_listing_id across all keyword/page fetches.
  Enrichment is optional: controlled by the ``enrich_detail`` constructor param.
  A ``detail_delay_seconds`` gap between detail fetches respects rate limits.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.turner_townsend")

_SEARCH_URL = "https://www.turnerandtownsend.com/api/careers/searchvacancies"
_CAREERS_REFERER = "https://www.turnerandtownsend.com/join-us/current-opportunities/"
_PAGE_SIZE = 50
_PAGE_DELAY_SECONDS = 3
_DETAIL_DELAY_SECONDS = 1.0
_REQUEST_TIMEOUT = 30.0
_DEFAULT_MAX_PAGES = 5
_EMPLOYER = "Turner & Townsend"
_SOURCE_NAME = "turner_townsend"

_POST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Content-Type": "application/json",
    "Referer": _CAREERS_REFERER,
    "Origin": "https://www.turnerandtownsend.com",
}

_DETAIL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_listing_id(ref: str) -> str | None:
    """Extract numeric listing ID from a SmartRecruiters posting URL.

    Example:
      https://api.smartrecruiters.com/v1/companies/TurnerTownsend/postings/744000131433769
    Returns:
      "744000131433769"
    """
    if not ref:
        return None
    candidate = ref.rstrip("/").split("/")[-1]
    return candidate if candidate.isdigit() else None


def _parse_posted_date(raw: str | None) -> datetime | None:
    """Parse ISO-8601 UTC date strings returned by the T&T search API.

    Confirmed format: ``2026-06-10T11:50:27.291Z``
    Also handles: ``2026-06-10T11:50:27.291+0000``, plain ``2026-06-10``.
    """
    if not raw:
        return None
    raw = raw.strip()
    iso_match = re.match(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([+-]\d{2}:?\d{2}|Z)?$", raw
    )
    if iso_match:
        try:
            return datetime.strptime(iso_match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    plain_match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if plain_match:
        try:
            return datetime.strptime(plain_match.group(1), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    logger.debug("turner_townsend: could not parse date string %r", raw)
    return None


def _get_custom_field(item: dict, field_label: str) -> str | None:
    """Return the valueLabel of the first customField entry matching field_label."""
    for cf in item.get("customField") or []:
        if cf.get("fieldLabel") == field_label:
            return cf.get("valueLabel") or None
    return None


def _build_location_raw(item: dict) -> str | None:
    """Build a human-readable location string from location.city and location.country."""
    loc = item.get("location") or {}
    city = (loc.get("city") or "").strip()
    country = (loc.get("country") or "").strip()
    parts = [p for p in (city, country) if p]
    return ", ".join(parts) if parts else None


def _content_item_to_raw_listing(item: dict) -> RawListing | None:
    """Map one listingModel.content entry to a RawListing.

    Returns None for entries with a missing title or non-extractable listing ID.
    contract_type_raw and description_raw are left None until optional enrichment.
    """
    title = (item.get("name") or "").strip()
    if not title:
        return None

    ref = (item.get("ref") or "").strip()
    listing_id = _extract_listing_id(ref)
    if not listing_id:
        return None

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=listing_id,
        url=ref,  # replaced by postingUrl if enriched
        title=title,
        employer=_EMPLOYER,
        agency=None,
        location_raw=_build_location_raw(item),
        description_raw=None,    # populated by enrichment if enabled
        posted_at=_parse_posted_date(item.get("releasedDate")),
        salary_raw=None,
        contract_type_raw=None,  # populated by enrichment if enabled
        metadata={
            "ref_url": ref,
            "department": (item.get("department") or {}).get("label") or None,
            "discipline": _get_custom_field(item, "Discipline"),
            "detail_fetched": False,
        },
    )


def _apply_detail(listing: RawListing, detail: dict) -> RawListing:
    """Return a new RawListing enriched with data from a SmartRecruiters detail response.

    Fields updated:
      typeOfEmployment.label                 → contract_type_raw
      jobAd.sections.jobDescription.text     → description_raw
      postingUrl                             → url
      experienceLevel.label                  → metadata.experience_level
    """
    contract_type_raw = (
        (detail.get("typeOfEmployment") or {}).get("label") or listing.contract_type_raw
    )
    sections = ((detail.get("jobAd") or {}).get("sections") or {})
    description_raw = (
        (sections.get("jobDescription") or {}).get("text") or listing.description_raw
    )
    posting_url = (detail.get("postingUrl") or "").strip() or listing.url
    experience_level = (detail.get("experienceLevel") or {}).get("label") or None

    updated_metadata = dict(listing.metadata)
    updated_metadata["detail_fetched"] = True
    if experience_level:
        updated_metadata["experience_level"] = experience_level

    return listing.model_copy(
        update={
            "url": posting_url,
            "contract_type_raw": contract_type_raw,
            "description_raw": description_raw,
            "metadata": updated_metadata,
        }
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class TurnerTownsendAdapter(SourceAdapter):
    """Adapter for Turner & Townsend careers (SmartRecruiters-backed JSON API).

    POSTs keyword + UK country filter to the T&T careers search API, paginates
    results, optionally enriches each listing with full detail from SmartRecruiters,
    and deduplicates across all keyword/page fetches by source_listing_id.

    robots.txt: permissive — only /umbraco/ and auth paths disallowed.
    Crawl-delay: honoured via inter-page sleep (_PAGE_DELAY_SECONDS).

    Config key: ``turner_townsend`` in config.toml.
    Constructor accepts all config.toml fields via ``**kwargs`` so unknown
    TOML keys do not raise errors.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        keywords_list: list[str] | None = None,
        crawl_delay: int = 3,
        max_pages_per_query: int = _DEFAULT_MAX_PAGES,
        enrich_detail: bool = False,
        detail_delay_seconds: float = _DETAIL_DELAY_SECONDS,
        **kwargs: object,
    ) -> None:
        self.keywords_list: list[str] = keywords_list or [
            "project manager",
            "project director",
        ]
        self.crawl_delay = crawl_delay
        self.max_pages_per_query = max_pages_per_query
        self.enrich_detail = enrich_detail
        self.detail_delay_seconds = detail_delay_seconds

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch T&T listings across keywords and pages, with optional enrichment.

        - Iterates each keyword in ``keywords_list``.
        - POSTs pages 1..N up to ``max_pages_per_query`` or paginationModel.totalPages.
        - Deduplicates by ``source_listing_id`` across all keyword/page fetches.
        - If ``enrich_detail`` is True, GETs each listing's SmartRecruiters ref URL.
        - Applies ``since`` filter on ``posted_at`` (best-effort; entries without
          a date are always included).
        - Returns [] and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}
        first_request = True

        try:
            async with httpx.AsyncClient(
                headers=_POST_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for keyword in self.keywords_list:
                    for page_num in range(1, self.max_pages_per_query + 1):
                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False

                        items, total_pages = await self._fetch_page(
                            client, keyword, page_num
                        )

                        if not items:
                            logger.debug(
                                "turner_townsend: no items on page %d for keyword %r — stopping.",
                                page_num,
                                keyword,
                            )
                            break

                        new_count = 0
                        for item in items:
                            listing = _content_item_to_raw_listing(item)
                            if listing is None:
                                continue
                            if since and listing.posted_at and listing.posted_at < since:
                                continue
                            if listing.source_listing_id not in seen:
                                seen[listing.source_listing_id] = listing
                                new_count += 1

                        logger.debug(
                            "turner_townsend: keyword=%r page=%d/%s → %d raw, %d new unique.",
                            keyword,
                            page_num,
                            total_pages if total_pages is not None else "?",
                            len(items),
                            new_count,
                        )

                        if total_pages is not None and page_num >= total_pages:
                            break

                if self.enrich_detail and seen:
                    await self._enrich_listings(client, seen)

        except Exception:
            logger.exception(
                "turner_townsend: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "turner_townsend: fetched %d unique listing(s) "
            "(keywords=%d, max_pages=%d, enriched=%s, since=%s).",
            len(listings),
            len(self.keywords_list),
            self.max_pages_per_query,
            self.enrich_detail,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        page: int,
    ) -> tuple[list[dict], int | None]:
        """POST one search page. Returns (items, total_pages_or_None).

        On HTTP or JSON error: logs warning, returns ([], None).
        """
        payload = {
            "page": page,
            "pageSize": _PAGE_SIZE,
            "query": keyword,
            "countries": ["United Kingdom"],
        }
        try:
            response = await client.post(_SEARCH_URL, json=payload)
        except httpx.RequestError as exc:
            logger.warning(
                "turner_townsend: request error on page %d for keyword %r: %s",
                page,
                keyword,
                exc,
            )
            return [], None

        if response.status_code != 200:
            logger.warning(
                "turner_townsend: HTTP %d for keyword %r page %d",
                response.status_code,
                keyword,
                page,
            )
            return [], None

        try:
            data = response.json()
        except Exception:
            logger.warning(
                "turner_townsend: JSON parse error for keyword %r page %d",
                keyword,
                page,
            )
            return [], None

        listing_model = data.get("listingModel") or {}
        items: list[dict] = listing_model.get("content") or []
        pagination = data.get("paginationModel") or {}
        total_pages: int | None = pagination.get("totalPages")

        return items, total_pages

    async def _enrich_listings(
        self,
        client: httpx.AsyncClient,
        seen: dict[str, RawListing],
    ) -> None:
        """Enrich each listing by GETting its SmartRecruiters ref URL in-place.

        Updates ``seen`` in-place.  Per-listing errors (timeout, HTTP error,
        JSON parse failure) are logged as warnings; the original listing is kept.
        """
        for i, (lid, listing) in enumerate(list(seen.items())):
            ref_url = listing.metadata.get("ref_url") or ""
            if not ref_url:
                continue

            if i > 0:
                await asyncio.sleep(self.detail_delay_seconds)

            try:
                response = await client.get(ref_url, headers=_DETAIL_HEADERS)
                if response.status_code != 200:
                    logger.warning(
                        "turner_townsend: enrichment HTTP %d for listing %s",
                        response.status_code,
                        lid,
                    )
                    continue
                detail = response.json()
                seen[lid] = _apply_detail(listing, detail)
                logger.debug(
                    "turner_townsend: enriched listing %s (%s)", lid, listing.title
                )
            except httpx.TimeoutException:
                logger.warning(
                    "turner_townsend: enrichment timeout for listing %s — skipping.", lid
                )
            except Exception:
                logger.warning(
                    "turner_townsend: enrichment failed for listing %s — skipping.",
                    lid,
                    exc_info=True,
                )
