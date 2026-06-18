"""RailwayPeople.com adapter — Next.js SSR / Jobiqo platform.

Strategy:
    GET the HTML search page, extract the ``<script id="__NEXT_DATA__"
    type="application/json">`` tag, JSON-parse its content, then recursively
    locate the first list of job-dicts inside ``props.pageProps``.  Each dict
    is mapped to a ``RawListing``.

Crawl-delay: 10 s between page fetches (robots.txt: ``Crawl-delay: 10``).
Pagination:   ``&page=N`` appended to the base search URL; capped at
              _MAX_PAGES for the MVP.
Since filter: applied client-side on ``posted_at`` after mapping.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx
from selectolax.parser import HTMLParser

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.railwaypeople")

_SEARCH_URL = (
    "https://www.railwaypeople.com/jobs"
    "?keywords=project+manager&jobtype=contract"
)
_MAX_PAGES = 5
_PAGE_DELAY_SECONDS = 10  # robots.txt mandates Crawl-delay: 10
_REQUEST_TIMEOUT = 30.0


def _build_rp_search_url(keyword: str) -> str:
    """Build a RailwayPeople search URL for a single keyword."""
    return (
        "https://www.railwaypeople.com/jobs"
        f"?keywords={quote_plus(keyword)}&jobtype=contract"
    )
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

# URL-like field names recognised in Jobiqo job dicts.
# NOTE: "url" in the actual JSON is a nested dict {"__typename": "Url", "path": "..."},
# not a bare string.  _extract_url() handles that explicitly.
_URL_FIELDS = frozenset({
    "url", "link", "href", "path", "slug",
    "jobUrl", "job_url", "permalink", "urlNoPrefix",
})

# Sentinel: JSON path where the jobs list was found (logged once per process).
_discovered_path: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_jobs_list(
    data: Any,
    path: str = "",
    _depth: int = 0,
) -> tuple[list[dict], str] | None:
    """Recursively search *data* for the first list of job-like dicts.

    A list qualifies when its first element is a ``dict`` that has both a
    ``"title"`` key AND at least one URL-like key (``_URL_FIELDS``).

    Returns ``(list_of_dicts, dotted_path)`` or ``None``.
    Depth-limited to 12 levels to avoid runaway recursion.
    """
    if _depth > 12:
        return None

    if isinstance(data, list):
        if (
            len(data) > 0
            and isinstance(data[0], dict)
            and "title" in data[0]
            and _URL_FIELDS.intersection(data[0].keys())
        ):
            return data, path
        # Recurse into list elements (dicts only, skip primitives)
        for i, item in enumerate(data):
            result = _find_jobs_list(item, f"{path}[{i}]", _depth + 1)
            if result is not None:
                return result

    elif isinstance(data, dict):
        # Probe well-known Jobiqo / Next.js keys first to short-circuit search.
        priority_keys = (
            "jobs", "initialJobs", "searchResults",
            "results", "listings", "data", "items",
        )
        for key in priority_keys:
            if key in data:
                child_path = f"{path}.{key}" if path else key
                result = _find_jobs_list(data[key], child_path, _depth + 1)
                if result is not None:
                    return result
        # Fallback: walk remaining keys.
        for key, value in data.items():
            if key in priority_keys:
                continue
            child_path = f"{path}.{key}" if path else key
            result = _find_jobs_list(value, child_path, _depth + 1)
            if result is not None:
                return result

    return None


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a Jobiqo/ISO date string to a UTC-aware datetime."""
    if not date_str:
        return None
    # Handle Unix timestamps (int stored as string)
    try:
        ts = float(date_str)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError):
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    logger.warning("RailwayPeople: could not parse date string: %r", date_str)
    return None


def _extract_url(
    job: dict[str, Any],
    base: str = "https://www.railwaypeople.com",
) -> str:
    """Best-effort canonical job URL from the job dict.

    Jobiqo __NEXT_DATA__ stores the URL as a nested dict:
      ``{"__typename": "Url", "path": "/job/some-title-12345"}``
    We prefer ``urlNoPrefix`` (identical flat string) then fall back to
    extracting ``url.path``, then plain string URL fields.
    """
    # 1. urlNoPrefix is a flat relative path string — easiest case.
    url_no_prefix = job.get("urlNoPrefix")
    if url_no_prefix and isinstance(url_no_prefix, str):
        return base.rstrip("/") + "/" + url_no_prefix.lstrip("/")

    # 2. url is a dict with a "path" key.
    url_obj = job.get("url")
    if isinstance(url_obj, dict):
        path = url_obj.get("path")
        if path and isinstance(path, str):
            return base.rstrip("/") + "/" + path.lstrip("/")

    # 3. Fall back to any plain-string URL-like field.
    for key in ("link", "href", "permalink", "path", "slug", "jobUrl", "job_url"):
        val = job.get(key)
        if val and isinstance(val, str):
            if val.startswith("http"):
                return val
            return base.rstrip("/") + "/" + val.lstrip("/")

    return base


def _extract_id(job: dict[str, Any]) -> str:
    """Best-effort source listing ID from the job dict."""
    for key in ("id", "jobId", "job_id", "nid", "uuid", "slug"):
        val = job.get(key)
        if val is not None:
            return str(val)
    return ""


def _scalar(value: Any) -> str | None:
    """Return *value* as a string if it is non-None, else None.

    When *value* is a dict, try to pull a human-readable label from it.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        label = value.get("name") or value.get("label") or value.get("text")
        return str(label) if label is not None else None
    return str(value)


# Fields extracted explicitly; everything else goes into metadata.
_MAPPED_KEYS = frozenset({
    "id", "jobId", "job_id", "nid", "uuid", "slug",
    "url", "urlNoPrefix", "link", "href", "permalink", "path",
    "title",
    # Jobiqo-specific location fields
    "address", "location", "locationName", "location_name", "city", "region",
    # Jobiqo-specific salary fields
    "salaryRange", "salaryRangeFree",
    "salary", "salaryDescription", "salary_description", "rate",
    # Jobiqo-specific employer fields
    "organization", "organizationProfile",
    "company", "employer", "companyName", "company_name", "advertiser", "client",
    # Contract type
    "contractType", "contract_type", "jobType", "employment_type",
    # Date fields
    "published", "publishedAt",
    "datePosted", "date_posted", "postedAt", "posted_at",
    "createdAt", "created_at",
    # Description (not present in search results, but mapped if ever added)
    "description", "jobDescription", "body", "summary",
})


def _map_job_to_raw_listing(job: dict[str, Any]) -> RawListing:
    """Map a Jobiqo ``__NEXT_DATA__`` job dict to a canonical ``RawListing``.

    Actual Jobiqo schema (confirmed 2026-06-14):
      - ``organization``       → employer name (flat string)
      - ``address``            → list of "City, Country" strings
      - ``url``                → nested dict  ``{"path": "/job/slug-id"}``
      - ``urlNoPrefix``        → flat relative path string (same as url.path)
      - ``published``          → ISO-8601 datetime with timezone offset
      - ``salaryRangeFree``    → dict with minSalary/maxSalary/currencyCode/salaryUnit
      - ``salaryRange``        → usually empty list in search results
      - no description field   → description_raw left as None (detail page only)
    """
    # --- employer ---
    employer = _scalar(
        job.get("organization")
        or job.get("company")
        or job.get("employer")
        or job.get("companyName")
        or job.get("company_name")
        or job.get("advertiser")
        or job.get("client")
    )
    # Also try organizationProfile.name as last fallback
    if employer is None:
        org_profile = job.get("organizationProfile")
        if isinstance(org_profile, dict):
            employer = _scalar(org_profile.get("name"))

    # --- location ---
    # address is a list like ["Manchester, UK", "York, UK"]
    address_list = job.get("address")
    if isinstance(address_list, list) and address_list:
        location = "; ".join(str(a) for a in address_list if a)
    else:
        location = _scalar(
            job.get("location")
            or job.get("locationName")
            or job.get("location_name")
            or job.get("city")
            or job.get("region")
        )

    # --- salary ---
    # salaryRangeFree carries structured min/max when the poster includes it.
    salary_raw: str | None = None
    salary_range_free = job.get("salaryRangeFree")
    if isinstance(salary_range_free, dict):
        min_sal = salary_range_free.get("minSalary")
        max_sal = salary_range_free.get("maxSalary")
        currency_code = salary_range_free.get("currencyCode") or "GBP"
        salary_unit = salary_range_free.get("salaryUnit")
        if min_sal is not None or max_sal is not None:
            sym = "£" if currency_code == "GBP" else currency_code
            if min_sal is not None and max_sal is not None and min_sal != max_sal:
                salary_raw = f"{sym}{min_sal} - {sym}{max_sal}"
            elif min_sal is not None:
                salary_raw = f"{sym}{min_sal}"
            else:
                salary_raw = f"{sym}{max_sal}"
            if salary_unit:
                salary_raw += f" {salary_unit}"
    # Fall back to plain salary/rate fields if present
    if salary_raw is None:
        salary_raw = _scalar(
            job.get("salary")
            or job.get("salaryDescription")
            or job.get("salary_description")
            or job.get("rate")
        )

    # --- contract type ---
    contract_type = _scalar(
        job.get("contractType")
        or job.get("contract_type")
        or job.get("jobType")
        or job.get("employment_type")
    ) or "Contract"  # default: we searched with jobtype=contract

    # --- description ---
    # Not available in search-result listings; only on individual job detail pages.
    description = _scalar(
        job.get("description")
        or job.get("jobDescription")
        or job.get("body")
        or job.get("summary")
    )

    # --- posted_at ---
    posted_at_raw = (
        job.get("published")
        or job.get("publishedAt")
        or job.get("datePosted")
        or job.get("date_posted")
        or job.get("postedAt")
        or job.get("posted_at")
        or job.get("createdAt")
        or job.get("created_at")
    )

    metadata = {k: v for k, v in job.items() if k not in _MAPPED_KEYS}

    return RawListing(
        source="railwaypeople",
        source_listing_id=_extract_id(job),
        url=_extract_url(job),
        title=job.get("title", ""),
        employer=employer,
        agency=None,
        location_raw=location,
        description_raw=description,
        posted_at=_parse_date(str(posted_at_raw) if posted_at_raw is not None else None),
        salary_raw=salary_raw,
        contract_type_raw=contract_type,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class RailwayPeopleAdapter(SourceAdapter):
    """Adapter for RailwayPeople.com (Next.js SSR / Jobiqo platform).

    Fetches the HTML search page, parses the ``<script id="__NEXT_DATA__">``
    JSON blob, recursively locates the jobs list inside ``props.pageProps``,
    and maps each job dict to a ``RawListing``.

    Supports a ``keywords_list`` of search terms (config-driven since v0.4).
    Each keyword generates an independent paginated query; results are
    deduplicated by ``source_listing_id`` before returning.

    Paginates up to ``_MAX_PAGES`` pages per keyword; sleeps ``_PAGE_DELAY_SECONDS``
    between requests (robots.txt ``Crawl-delay: 10``).
    """

    name = "railwaypeople"

    def __init__(
        self,
        crawl_delay: int = 10,
        keywords_list: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        self.crawl_delay = crawl_delay
        self._keywords_list: list[str] = keywords_list or ["project manager"]

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch contract PM listings from RailwayPeople.com.

        Iterates over ``_keywords_list``, running a paginated query per keyword.
        Results are deduplicated by ``source_listing_id``.
        Returns [] on any unrecoverable error — never raises.
        Applies *since* filter client-side on ``posted_at``.
        """
        seen_ids: set[str] = set()
        all_listings: list[RawListing] = []

        for kw_idx, keyword in enumerate(self._keywords_list):
            base_url = _build_rp_search_url(keyword)
            kw_listings: list[RawListing] = []
            try:
                async with httpx.AsyncClient(
                    headers=_HEADERS,
                    timeout=_REQUEST_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    for page_num in range(1, _MAX_PAGES + 1):
                        url = (
                            base_url
                            if page_num == 1
                            else f"{base_url}&page={page_num}"
                        )
                        page_listings, found_count = await self._fetch_page(
                            client, url, page_num
                        )

                        if found_count == 0 and page_num == 1:
                            break

                        if found_count == 0:
                            logger.info(
                                "RailwayPeople[%s]: page %d returned 0 listings"
                                " — stopping pagination.",
                                keyword,
                                page_num,
                            )
                            break

                        for raw in page_listings:
                            if since and raw.posted_at and raw.posted_at < since:
                                continue
                            if raw.source_listing_id not in seen_ids:
                                seen_ids.add(raw.source_listing_id)
                                kw_listings.append(raw)

                        if page_num < _MAX_PAGES and found_count > 0:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)

            except Exception:
                logger.exception(
                    "RailwayPeople[%s] fetch failed — %d partial result(s).",
                    keyword,
                    len(kw_listings),
                )

            all_listings.extend(kw_listings)

            # Sleep between keyword queries (not after the last one).
            if kw_idx < len(self._keywords_list) - 1:
                await asyncio.sleep(_PAGE_DELAY_SECONDS)

        logger.info(
            "RailwayPeople: fetched %d unique listing(s) across %d keyword(s) (since=%s).",
            len(all_listings),
            len(self._keywords_list),
            since,
        )
        return all_listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        page_num: int,
    ) -> tuple[list[RawListing], int]:
        """Fetch and parse one search-result page.

        Returns ``(listings, count_found_in_json)``.
        Returns ``([], 0)`` on any error — never raises.
        """
        global _discovered_path

        # --- HTTP fetch ---
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "RailwayPeople HTTP error (page=%d, url=%s): %s",
                page_num, url, exc,
            )
            return [], 0
        except Exception:
            logger.exception(
                "RailwayPeople request failed (page=%d, url=%s).",
                page_num, url,
            )
            return [], 0

        # --- Parse __NEXT_DATA__ from HTML ---
        try:
            tree = HTMLParser(response.text)
        except Exception:
            logger.exception(
                "RailwayPeople: HTMLParser failed (page=%d).", page_num
            )
            return [], 0

        script_node = tree.css_first("script#__NEXT_DATA__")
        if script_node is None:
            logger.warning(
                "RailwayPeople: <script id='__NEXT_DATA__'> not found on"
                " page %d — site structure may have changed.",
                page_num,
            )
            return [], 0

        try:
            next_data: dict[str, Any] = json.loads(script_node.text())
        except json.JSONDecodeError as exc:
            logger.warning(
                "RailwayPeople: JSON decode error (page=%d): %s",
                page_num, exc,
            )
            return [], 0

        # --- Locate jobs list inside __NEXT_DATA__ ---
        page_props: dict[str, Any] = (
            next_data.get("props", {}).get("pageProps", {})
        )

        # Try pageProps first (the canonical Next.js location).
        result = _find_jobs_list(page_props, "props.pageProps")
        # Widen to the whole blob if pageProps search came up empty.
        if result is None:
            result = _find_jobs_list(next_data, "")

        if result is None:
            top_keys = list(page_props.keys()) if page_props else list(next_data.keys())
            logger.warning(
                "RailwayPeople: could not locate jobs list inside"
                " __NEXT_DATA__ (page=%d). Top-level pageProps keys: %s",
                page_num,
                top_keys,
            )
            return [], 0

        jobs, path = result

        # Log the discovered path the first time (or if it changes).
        if _discovered_path != path:
            _discovered_path = path
            logger.info(
                "RailwayPeople: jobs list found at JSON path '%s'"
                " (count=%d, page=%d).",
                path, len(jobs), page_num,
            )

        # --- Map each job dict to RawListing ---
        listings: list[RawListing] = []
        for job in jobs:
            try:
                listings.append(_map_job_to_raw_listing(job))
            except Exception:
                logger.exception(
                    "RailwayPeople: failed to map job dict — skipping."
                )

        return listings, len(jobs)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    adapter = RailwayPeopleAdapter()
    results = asyncio.run(adapter.fetch())
    print(f"Fetched {len(results)} listing(s) from RailwayPeople.")
    for listing in results[:2]:
        print(
            f"  [{listing.source_listing_id}] '{listing.title}'"
            f" | {listing.employer}"
            f" | {listing.location_raw}"
            f" | {listing.salary_raw}"
            f" | posted={listing.posted_at}"
        )
    if not results:
        print("No results — check logs above for details.", file=sys.stderr)
        sys.exit(1)
