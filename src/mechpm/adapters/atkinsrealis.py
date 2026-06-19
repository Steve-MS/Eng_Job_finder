"""AtkinsRéalis careers adapter — ConnectID-powered JSON API.

Overview
--------
AtkinsRéalis (formerly Atkins + SNC-Lavalin) exposes a public JSON API via
their ConnectID-powered careers portal at ``atkinsats-prod-api.connectid.cloud``.
No human-facing scraping is required.

Discovery (2026-06-19)
-----------------------
- GET  https://atkinsats-prod-api.connectid.cloud/api/jobs/token
  → {"token": "<JWT>"}  — public endpoint, no credentials needed.
  JWT lifetime: 3 600 s (1 hour); refresh buffer applied at 120 s before expiry.
- POST https://atkinsats-prod-api.connectid.cloud/api/jobs/jobs
  Headers: Authorization: Bearer <token>, Content-Type: application/json
  Body:    {"keyword": "...", "location": "United Kingdom",
            "page": 1, "pageSize": 50}
  Response: {
    "jobs":   [{...}, ...],
    "facets": {...},
    "meta":   {"totalCount": N, "perPage": P, "totalPages": T, "currentPage": C}
  }

Field map (jobs[])
-------------------
  id                          → source_listing_id (int → str)
  job_posting_title           → title
  external_posting_url        → url  (Workday direct link; fallback constructed if absent)
  cities + countries          → location_raw
  salary_min/max/currency     → salary_raw (constructed if any value present)
  created_at                  → posted_at  (ISO-8601 UTC)
  time_type                   → contract_type_raw  ("Full time", "Part time", …)
  job_overview                → description_raw  (HTML; primary section)
  job_responsibilities        → description_raw  (appended)
  person_requirements         → description_raw  (appended)
  job_area                    → metadata.job_area
  sub_job_area                → metadata.sub_job_area
  discipline                  → metadata.discipline
  job_requisition_id          → metadata.job_requisition_id
  is_remote                   → metadata.is_remote
  market_sector               → metadata.market_sector

Pagination
----------
  POST page=1..N until meta.currentPage >= meta.totalPages,
  or ``max_pages_per_query`` is exhausted.  Default pageSize: 50.

Token refresh
-------------
  Token cached in ``_token`` / ``_token_acquired_at`` instance fields.
  ``_ensure_token()`` refreshes automatically when the token age exceeds
  TOKEN_TTL_SECONDS − TOKEN_REFRESH_BUFFER (3 480 s ≈ 58 minutes).
  A 401 from the search endpoint also invalidates the cache immediately.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.atkinsrealis")

_API_BASE = "https://atkinsats-prod-api.connectid.cloud"
_PAGE_SIZE = 50
_PAGE_DELAY_SECONDS = 2
_REQUEST_TIMEOUT = 30.0
_DEFAULT_MAX_PAGES = 10
_EMPLOYER = "AtkinsRéalis"
_SOURCE_NAME = "atkinsrealis"

# Tokens last 3 600 s; refresh with 120 s buffer to avoid mid-run expiry.
TOKEN_TTL_SECONDS = 3600
TOKEN_REFRESH_BUFFER = 120

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://careers.atkinsrealis.com",
    "Referer": "https://careers.atkinsrealis.com/",
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_posted_date(raw: str | None) -> datetime | None:
    """Parse ISO-8601 UTC date strings returned by the ConnectID API.

    Confirmed formats from live probe (2026-06-19):
      ``2026-06-19T07:02:24.341Z``
      ``2026-06-19T00:00:00.000Z``
    Also handles plain ``YYYY-MM-DD``.
    """
    if not raw:
        return None
    raw = raw.strip()
    iso_match = re.match(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([+-]\d{2}:?\d{2}|Z)?$",
        raw,
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
    logger.debug("atkinsrealis: could not parse date string %r", raw)
    return None


def _build_location_raw(job: dict) -> str | None:
    """Construct a human-readable location string from ``cities`` and ``countries``."""
    city = (job.get("cities") or "").strip()
    country = (job.get("countries") or "").strip()
    parts = [p for p in (city, country) if p]
    return ", ".join(parts) if parts else None


def _build_salary_raw(job: dict) -> str | None:
    """Construct a salary string from ``salary_min``, ``salary_max``, and ``salary_currency``.

    Returns None when all three fields are absent or None (the common case for
    AtkinsRéalis roles which do not advertise compensation publicly).
    """
    salary_min = job.get("salary_min")
    salary_max = job.get("salary_max")
    currency = (job.get("salary_currency") or "").strip()
    if salary_min is None and salary_max is None:
        return None
    parts: list[str] = []
    if currency:
        parts.append(currency)
    if salary_min is not None and salary_max is not None:
        parts.append(f"{salary_min}\u2013{salary_max}")
    elif salary_min is not None:
        parts.append(f"from {salary_min}")
    elif salary_max is not None:
        parts.append(f"up to {salary_max}")
    return " ".join(parts) if parts else None


def _build_description_raw(job: dict) -> str | None:
    """Concatenate HTML description sections in logical reading order.

    Combines ``job_overview``, ``job_responsibilities``, and
    ``person_requirements`` to form a single description block.
    Returns None when all sections are empty.
    """
    sections = [
        job.get("job_overview") or "",
        job.get("job_responsibilities") or "",
        job.get("person_requirements") or "",
    ]
    combined = "\n".join(s for s in sections if s.strip())
    return combined if combined.strip() else None


def _job_to_raw_listing(job: dict, api_base: str = _API_BASE) -> RawListing | None:
    """Map one ``jobs[]`` entry to a RawListing.

    Returns None for entries with a missing title or id.
    The ``url`` falls back to a constructed careers-portal URL when
    ``external_posting_url`` is absent.
    """
    title = (job.get("job_posting_title") or "").strip()
    if not title:
        return None

    job_id = job.get("id")
    if job_id is None:
        return None
    source_listing_id = str(job_id)

    url = (job.get("external_posting_url") or "").strip()
    if not url:
        req_id = (job.get("job_requisition_id") or "").strip()
        if req_id:
            url = f"https://careers.atkinsrealis.com/job/{req_id}"
        else:
            url = f"{api_base.rstrip('/')}/job/{source_listing_id}"

    return RawListing(
        source=_SOURCE_NAME,
        source_listing_id=source_listing_id,
        url=url,
        title=title,
        employer=_EMPLOYER,
        agency=None,
        location_raw=_build_location_raw(job),
        description_raw=_build_description_raw(job),
        posted_at=_parse_posted_date(job.get("created_at")),
        salary_raw=_build_salary_raw(job),
        contract_type_raw=(job.get("time_type") or None),
        metadata={
            "job_requisition_id": job.get("job_requisition_id"),
            "job_area": job.get("job_area"),
            "sub_job_area": job.get("sub_job_area"),
            "discipline": job.get("discipline"),
            "market_sector": job.get("market_sector"),
            "is_remote": job.get("is_remote"),
        },
    )


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class AtkinsRealisAdapter(SourceAdapter):
    """Adapter for AtkinsRéalis careers (ConnectID-powered JSON API).

    Acquires a short-lived Bearer token from the public token endpoint, then
    POSTs keyword + UK location filter to the jobs search endpoint.  Paginates
    all results per keyword, deduplicates by ``source_listing_id`` across all
    keyword/page fetches, and refreshes the token automatically when it nears
    expiry.

    robots.txt: permissive — only ``/umbraco/`` and auth paths disallowed on
    the parent careers site.  The API subdomain has no robots.txt restriction.
    Crawl-delay honoured via ``_PAGE_DELAY_SECONDS`` inter-page sleep.

    Config key: ``atkinsrealis`` in config.toml.
    Constructor accepts all config.toml fields via ``**kwargs`` so unknown
    TOML keys do not raise errors.
    """

    name = _SOURCE_NAME

    def __init__(
        self,
        keywords_list: list[str] | None = None,
        crawl_delay: int = 2,
        max_pages_per_query: int = _DEFAULT_MAX_PAGES,
        api_base: str = _API_BASE,
        **kwargs: object,
    ) -> None:
        self.keywords_list: list[str] = keywords_list or [
            "project manager",
            "project director",
        ]
        self.crawl_delay = crawl_delay
        self.max_pages_per_query = max_pages_per_query
        self.api_base = api_base.rstrip("/")
        self._token: str | None = None
        self._token_acquired_at: datetime | None = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _token_is_valid(self) -> bool:
        """Return True if the cached token is still within its usable lifetime."""
        if self._token is None or self._token_acquired_at is None:
            return False
        age = (datetime.now(timezone.utc) - self._token_acquired_at).total_seconds()
        return age < (TOKEN_TTL_SECONDS - TOKEN_REFRESH_BUFFER)

    async def _acquire_token(self, client: httpx.AsyncClient) -> str | None:
        """Fetch a fresh Bearer token from the token endpoint.

        Caches the token string and acquisition timestamp in ``_token`` /
        ``_token_acquired_at``.  Returns the token on success, None on failure.
        """
        token_url = f"{self.api_base}/api/jobs/token"
        try:
            response = await client.get(token_url)
        except httpx.RequestError as exc:
            logger.warning("atkinsrealis: token request error: %s", exc)
            return None

        if response.status_code != 200:
            logger.warning(
                "atkinsrealis: token endpoint returned HTTP %d",
                response.status_code,
            )
            return None

        try:
            data = response.json()
        except Exception:
            logger.warning("atkinsrealis: could not parse token response as JSON")
            return None

        token = (data.get("token") or "").strip()
        if not token:
            logger.warning("atkinsrealis: empty or missing 'token' field in response")
            return None

        self._token = token
        self._token_acquired_at = datetime.now(timezone.utc)
        logger.debug("atkinsrealis: acquired new Bearer token.")
        return token

    async def _ensure_token(self, client: httpx.AsyncClient) -> str | None:
        """Return a valid cached token, refreshing from the API if necessary."""
        if self._token_is_valid():
            return self._token
        return await self._acquire_token(client)

    # ------------------------------------------------------------------
    # Fetch logic
    # ------------------------------------------------------------------

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch AtkinsRéalis listings across keywords and pages.

        - Iterates each keyword in ``keywords_list``.
        - POSTs pages 1..N up to ``max_pages_per_query`` or ``meta.totalPages``.
        - Deduplicates by ``source_listing_id`` across all keyword/page fetches.
        - Refreshes the Bearer token automatically before it expires.
        - Applies ``since`` filter on ``posted_at`` (best-effort; entries without
          a date are always included).
        - Returns [] and logs a warning on any unrecoverable error.
        """
        seen: dict[str, RawListing] = {}

        try:
            async with httpx.AsyncClient(
                headers=_BASE_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                token = await self._ensure_token(client)
                if not token:
                    logger.error(
                        "atkinsrealis: could not acquire token — aborting fetch."
                    )
                    return []

                first_request = True
                for keyword in self.keywords_list:
                    for page_num in range(1, self.max_pages_per_query + 1):
                        if not first_request:
                            await asyncio.sleep(_PAGE_DELAY_SECONDS)
                        first_request = False

                        token = await self._ensure_token(client)
                        if not token:
                            logger.warning(
                                "atkinsrealis: token refresh failed — stopping."
                            )
                            break

                        jobs, total_pages = await self._fetch_page(
                            client, token, keyword, page_num
                        )

                        if not jobs:
                            logger.debug(
                                "atkinsrealis: no jobs on page %d for keyword %r — stopping.",
                                page_num,
                                keyword,
                            )
                            break

                        new_count = 0
                        for job in jobs:
                            listing = _job_to_raw_listing(job, api_base=self.api_base)
                            if listing is None:
                                continue
                            if since and listing.posted_at and listing.posted_at < since:
                                continue
                            if listing.source_listing_id not in seen:
                                seen[listing.source_listing_id] = listing
                                new_count += 1

                        logger.debug(
                            "atkinsrealis: keyword=%r page=%d/%s → %d raw, %d new unique.",
                            keyword,
                            page_num,
                            total_pages if total_pages is not None else "?",
                            len(jobs),
                            new_count,
                        )

                        if total_pages is not None and page_num >= total_pages:
                            break

        except Exception:
            logger.exception(
                "atkinsrealis: unexpected error in fetch() — "
                "returning partial results (%d so far).",
                len(seen),
            )
            return list(seen.values())

        listings = list(seen.values())
        logger.info(
            "atkinsrealis: fetched %d unique listing(s) "
            "(keywords=%d, max_pages=%d, since=%s).",
            len(listings),
            len(self.keywords_list),
            self.max_pages_per_query,
            since,
        )
        return listings

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        token: str,
        keyword: str,
        page: int,
    ) -> tuple[list[dict], int | None]:
        """POST one search page. Returns (jobs, total_pages_or_None).

        On HTTP or JSON error: logs warning, returns ([], None).
        A 401 response additionally invalidates the token cache so the next
        call to ``_ensure_token`` triggers a fresh acquisition.
        """
        search_url = f"{self.api_base}/api/jobs/jobs"
        payload = {
            "keyword": keyword,
            "location": "United Kingdom",
            "page": page,
            "pageSize": _PAGE_SIZE,
        }
        auth_headers = {"Authorization": f"Bearer {token}"}
        try:
            response = await client.post(
                search_url, json=payload, headers=auth_headers
            )
        except httpx.RequestError as exc:
            logger.warning(
                "atkinsrealis: request error on page %d for keyword %r: %s",
                page,
                keyword,
                exc,
            )
            return [], None

        if response.status_code == 401:
            logger.warning(
                "atkinsrealis: HTTP 401 on page %d for keyword %r — "
                "invalidating cached token.",
                page,
                keyword,
            )
            self._token = None
            self._token_acquired_at = None
            return [], None

        if response.status_code != 200:
            logger.warning(
                "atkinsrealis: HTTP %d for keyword %r page %d",
                response.status_code,
                keyword,
                page,
            )
            return [], None

        try:
            data = response.json()
        except Exception:
            logger.warning(
                "atkinsrealis: JSON parse error for keyword %r page %d",
                keyword,
                page,
            )
            return [], None

        jobs: list[dict] = data.get("jobs") or []
        meta: dict = data.get("meta") or {}
        total_pages: int | None = meta.get("totalPages")

        return jobs, total_pages
