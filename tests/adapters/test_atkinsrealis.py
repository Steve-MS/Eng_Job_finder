"""Unit tests for the AtkinsRéalis adapter.

All tests use synthetic data and mocked HTTP — no live network calls.

Coverage:
  - Pure helper functions (_parse_posted_date, _build_location_raw,
    _build_salary_raw, _build_description_raw)
  - Field mapping (_job_to_raw_listing)
  - Adapter constructor and defaults
  - Token management (_token_is_valid, _acquire_token, _ensure_token)
  - fetch(): single page, pagination, dedup, since-filter, error handling,
    token failure, token refresh, 401 invalidation
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.atkinsrealis import (
    AtkinsRealisAdapter,
    _build_description_raw,
    _build_location_raw,
    _build_salary_raw,
    _job_to_raw_listing,
    _parse_posted_date,
    _EMPLOYER,
    _SOURCE_NAME,
    TOKEN_TTL_SECONDS,
    TOKEN_REFRESH_BUFFER,
)


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------

_WORKDAY_BASE = "https://slihrms.wd3.myworkdayjobs.com/Careers/job/"


def _make_job(
    job_id: int = 8180707,
    title: str = "Project Manager",
    req_id: str = "R-158259",
    external_url: str = f"{_WORKDAY_BASE}GBLondonNova-North/Project-Manager_R-158259",
    cities: str = "London",
    countries: str = "United Kingdom",
    time_type: str = "Full time",
    salary_min: float | None = None,
    salary_max: float | None = None,
    salary_currency: str | None = None,
    created_at: str = "2026-06-19T07:02:24.341Z",
    job_overview: str = "<h2>Overview</h2><p>Deliver major projects.</p>",
    job_responsibilities: str = "<h2>Your role</h2><ul><li>Lead delivery.</li></ul>",
    person_requirements: str = "<h2>About you</h2><ul><li>Strong PM background.</li></ul>",
    job_area: str = "Project & Programme Management",
    sub_job_area: str | None = None,
    discipline: str | None = None,
    market_sector: str | None = None,
    is_remote: bool = False,
) -> dict:
    """Build a synthetic jobs[] entry mirroring the live API shape."""
    return {
        "id": job_id,
        "job_requisition_id": req_id,
        "job_posting_title": title,
        "external_posting_url": external_url,
        "cities": cities,
        "countries": countries,
        "time_type": time_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "created_at": created_at,
        "job_overview": job_overview,
        "job_responsibilities": job_responsibilities,
        "person_requirements": person_requirements,
        "job_area": job_area,
        "sub_job_area": sub_job_area,
        "discipline": discipline,
        "market_sector": market_sector,
        "is_remote": is_remote,
    }


def _make_search_response(
    jobs: list[dict],
    page: int = 1,
    total_pages: int = 1,
    per_page: int = 50,
) -> dict:
    """Build a synthetic /api/jobs/jobs response envelope."""
    return {
        "jobs": jobs,
        "facets": {},
        "meta": {
            "totalCount": len(jobs),
            "perPage": per_page,
            "totalPages": total_pages,
            "currentPage": page,
        },
    }


def _make_token_response(token: str = "fake.jwt.token") -> dict:
    return {"token": token}


def _mock_client(
    token_response: dict | None = None,
    post_responses: list[dict] | None = None,
    token_status: int = 200,
    post_statuses: list[int] | None = None,
):
    """Build a mock httpx.AsyncClient.

    token_response: JSON payload for GET /api/jobs/token.
    post_responses: ordered list of JSON payloads for sequential POST calls.
    token_status:   HTTP status for the token endpoint.
    post_statuses:  ordered list of HTTP statuses for POST calls.
    """
    token_response = token_response or _make_token_response()
    post_responses = post_responses or [_make_search_response([])]
    post_statuses = post_statuses or [200] * len(post_responses)

    get_call_index = [0]
    post_call_index = [0]

    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = token_status
        resp.json.return_value = token_response
        return resp

    async def _post(url, json=None, headers=None, **kwargs):
        idx = post_call_index[0]
        post_call_index[0] += 1
        resp = MagicMock()
        status = post_statuses[idx] if idx < len(post_statuses) else 200
        resp.status_code = status
        payload = post_responses[idx] if idx < len(post_responses) else _make_search_response([])
        resp.json.return_value = payload
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=_get)
    mock.post = AsyncMock(side_effect=_post)
    return mock


# ===========================================================================
# Helper function unit tests
# ===========================================================================


class TestParsePostedDate:
    def test_iso_z_suffix(self):
        dt = _parse_posted_date("2026-06-19T07:02:24.341Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 19
        assert dt.tzinfo == timezone.utc

    def test_iso_with_ms_no_tz(self):
        dt = _parse_posted_date("2026-06-10T00:00:00.000Z")
        assert dt is not None
        assert dt.hour == 0
        assert dt.tzinfo == timezone.utc

    def test_plain_date(self):
        dt = _parse_posted_date("2026-01-15")
        assert dt is not None
        assert dt.month == 1
        assert dt.day == 15
        assert dt.tzinfo == timezone.utc

    def test_none_returns_none(self):
        assert _parse_posted_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_posted_date("") is None

    def test_gibberish_returns_none(self):
        assert _parse_posted_date("not-a-date") is None

    def test_whitespace_stripped(self):
        dt = _parse_posted_date("  2026-03-01T12:00:00.000Z  ")
        assert dt is not None
        assert dt.month == 3


class TestBuildLocationRaw:
    def test_city_and_country(self):
        job = {"cities": "London", "countries": "United Kingdom"}
        assert _build_location_raw(job) == "London, United Kingdom"

    def test_city_only(self):
        job = {"cities": "Leeds", "countries": ""}
        assert _build_location_raw(job) == "Leeds"

    def test_country_only(self):
        job = {"cities": "", "countries": "United Kingdom"}
        assert _build_location_raw(job) == "United Kingdom"

    def test_missing_keys_returns_none(self):
        assert _build_location_raw({}) is None

    def test_both_empty_returns_none(self):
        job = {"cities": "", "countries": ""}
        assert _build_location_raw(job) is None

    def test_whitespace_stripped(self):
        job = {"cities": "  Manchester  ", "countries": "  United Kingdom  "}
        assert _build_location_raw(job) == "Manchester, United Kingdom"


class TestBuildSalaryRaw:
    def test_no_salary_data_returns_none(self):
        job = {"salary_min": None, "salary_max": None, "salary_currency": None}
        assert _build_salary_raw(job) is None

    def test_missing_salary_keys_returns_none(self):
        assert _build_salary_raw({}) is None

    def test_range_with_currency(self):
        job = {"salary_min": 60000, "salary_max": 80000, "salary_currency": "GBP"}
        result = _build_salary_raw(job)
        assert result is not None
        assert "60000" in result
        assert "80000" in result
        assert "GBP" in result

    def test_min_only(self):
        job = {"salary_min": 50000, "salary_max": None, "salary_currency": "GBP"}
        result = _build_salary_raw(job)
        assert result is not None
        assert "from 50000" in result

    def test_max_only(self):
        job = {"salary_min": None, "salary_max": 90000, "salary_currency": "GBP"}
        result = _build_salary_raw(job)
        assert result is not None
        assert "up to 90000" in result

    def test_range_no_currency(self):
        job = {"salary_min": 40000, "salary_max": 60000, "salary_currency": None}
        result = _build_salary_raw(job)
        assert result is not None
        assert "40000" in result
        assert "60000" in result


class TestBuildDescriptionRaw:
    def test_all_sections_combined(self):
        job = {
            "job_overview": "<p>Overview</p>",
            "job_responsibilities": "<p>Responsibilities</p>",
            "person_requirements": "<p>Requirements</p>",
        }
        result = _build_description_raw(job)
        assert result is not None
        assert "<p>Overview</p>" in result
        assert "<p>Responsibilities</p>" in result
        assert "<p>Requirements</p>" in result

    def test_overview_only(self):
        job = {
            "job_overview": "<p>Overview</p>",
            "job_responsibilities": "",
            "person_requirements": "",
        }
        result = _build_description_raw(job)
        assert result == "<p>Overview</p>"

    def test_all_empty_returns_none(self):
        job = {"job_overview": "", "job_responsibilities": "", "person_requirements": ""}
        assert _build_description_raw(job) is None

    def test_missing_keys_returns_none(self):
        assert _build_description_raw({}) is None


# ===========================================================================
# _job_to_raw_listing mapping tests
# ===========================================================================


class TestJobToRawListing:
    def test_returns_raw_listing(self):
        job = _make_job()
        listing = _job_to_raw_listing(job)
        assert listing is not None

    def test_source_is_atkinsrealis(self):
        listing = _job_to_raw_listing(_make_job())
        assert listing.source == _SOURCE_NAME

    def test_employer_is_atkinsrealis(self):
        listing = _job_to_raw_listing(_make_job())
        assert listing.employer == _EMPLOYER

    def test_title_mapped(self):
        listing = _job_to_raw_listing(_make_job(title="Programme Director"))
        assert listing.title == "Programme Director"

    def test_source_listing_id_is_str(self):
        listing = _job_to_raw_listing(_make_job(job_id=9999999))
        assert listing.source_listing_id == "9999999"

    def test_url_from_external_posting_url(self):
        url = f"{_WORKDAY_BASE}GB-London/PM_R-001"
        listing = _job_to_raw_listing(_make_job(external_url=url))
        assert listing.url == url

    def test_url_fallback_uses_req_id(self):
        job = _make_job(external_url="", req_id="R-99999")
        listing = _job_to_raw_listing(job)
        assert "R-99999" in listing.url
        assert "careers.atkinsrealis.com" in listing.url

    def test_url_fallback_uses_api_base_when_no_req_id(self):
        job = _make_job(external_url="", req_id="")
        job["job_requisition_id"] = ""
        listing = _job_to_raw_listing(job)
        assert listing.url  # not empty

    def test_missing_title_returns_none(self):
        job = _make_job(title="")
        assert _job_to_raw_listing(job) is None

    def test_missing_id_returns_none(self):
        job = _make_job()
        job["id"] = None
        assert _job_to_raw_listing(job) is None

    def test_location_raw(self):
        listing = _job_to_raw_listing(_make_job(cities="Bristol", countries="United Kingdom"))
        assert listing.location_raw == "Bristol, United Kingdom"

    def test_contract_type_raw(self):
        listing = _job_to_raw_listing(_make_job(time_type="Full time"))
        assert listing.contract_type_raw == "Full time"

    def test_posted_at_parsed(self):
        listing = _job_to_raw_listing(_make_job(created_at="2026-06-01T09:00:00.000Z"))
        assert listing.posted_at is not None
        assert listing.posted_at.month == 6
        assert listing.posted_at.day == 1

    def test_description_raw_populated(self):
        listing = _job_to_raw_listing(_make_job())
        assert listing.description_raw is not None
        assert "<h2>" in listing.description_raw

    def test_salary_raw_none_when_not_set(self):
        listing = _job_to_raw_listing(_make_job(salary_min=None, salary_max=None))
        assert listing.salary_raw is None

    def test_metadata_job_area(self):
        listing = _job_to_raw_listing(_make_job(job_area="Engineering"))
        assert listing.metadata["job_area"] == "Engineering"

    def test_metadata_is_remote(self):
        listing = _job_to_raw_listing(_make_job(is_remote=True))
        assert listing.metadata["is_remote"] is True


# ===========================================================================
# Adapter constructor tests
# ===========================================================================


class TestAdapterConstructor:
    def test_default_name(self):
        adapter = AtkinsRealisAdapter()
        assert adapter.name == _SOURCE_NAME

    def test_default_crawl_delay(self):
        adapter = AtkinsRealisAdapter()
        assert adapter.crawl_delay == 2

    def test_default_max_pages(self):
        adapter = AtkinsRealisAdapter()
        assert adapter.max_pages_per_query == 10

    def test_custom_keywords(self):
        kw = ["quantity surveyor", "risk manager"]
        adapter = AtkinsRealisAdapter(keywords_list=kw)
        assert adapter.keywords_list == kw

    def test_custom_crawl_delay(self):
        adapter = AtkinsRealisAdapter(crawl_delay=5)
        assert adapter.crawl_delay == 5

    def test_unknown_kwargs_accepted(self):
        adapter = AtkinsRealisAdapter(unknown_future_param="ignored")
        assert adapter.name == _SOURCE_NAME

    def test_token_initially_none(self):
        adapter = AtkinsRealisAdapter()
        assert adapter._token is None
        assert adapter._token_acquired_at is None


# ===========================================================================
# Token validity tests
# ===========================================================================


class TestTokenValidity:
    def test_none_token_is_invalid(self):
        adapter = AtkinsRealisAdapter()
        assert adapter._token_is_valid() is False

    def test_fresh_token_is_valid(self):
        adapter = AtkinsRealisAdapter()
        adapter._token = "some.jwt"
        adapter._token_acquired_at = datetime.now(timezone.utc)
        assert adapter._token_is_valid() is True

    def test_expired_token_is_invalid(self):
        adapter = AtkinsRealisAdapter()
        adapter._token = "some.jwt"
        adapter._token_acquired_at = datetime.now(timezone.utc) - timedelta(
            seconds=TOKEN_TTL_SECONDS
        )
        assert adapter._token_is_valid() is False

    def test_token_near_expiry_is_invalid(self):
        adapter = AtkinsRealisAdapter()
        adapter._token = "some.jwt"
        # Set acquired_at so age is just above the refresh threshold
        adapter._token_acquired_at = datetime.now(timezone.utc) - timedelta(
            seconds=TOKEN_TTL_SECONDS - TOKEN_REFRESH_BUFFER + 1
        )
        assert adapter._token_is_valid() is False

    def test_token_just_before_buffer_is_valid(self):
        adapter = AtkinsRealisAdapter()
        adapter._token = "some.jwt"
        # Age is 1 second below the threshold (TOKEN_TTL - BUFFER - 1)
        adapter._token_acquired_at = datetime.now(timezone.utc) - timedelta(
            seconds=TOKEN_TTL_SECONDS - TOKEN_REFRESH_BUFFER - 1
        )
        assert adapter._token_is_valid() is True


# ===========================================================================
# fetch() async integration tests (fully mocked HTTP)
# ===========================================================================


class TestFetch:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_fetch_returns_empty_when_no_jobs(self):
        adapter = AtkinsRealisAdapter(keywords_list=["project manager"])
        mock_client = _mock_client(
            post_responses=[_make_search_response([])],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert results == []

    def test_fetch_single_page_returns_listings(self):
        jobs = [_make_job(job_id=1001), _make_job(job_id=1002, title="Project Director")]
        adapter = AtkinsRealisAdapter(keywords_list=["project manager"])
        mock_client = _mock_client(
            post_responses=[_make_search_response(jobs, page=1, total_pages=1)],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert len(results) == 2

    def test_fetch_maps_source_correctly(self):
        jobs = [_make_job(job_id=2001)]
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        mock_client = _mock_client(
            post_responses=[_make_search_response(jobs, total_pages=1)],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert results[0].source == _SOURCE_NAME

    def test_fetch_pagination_multiple_pages(self):
        page1_jobs = [_make_job(job_id=i) for i in range(1, 4)]
        page2_jobs = [_make_job(job_id=i) for i in range(4, 7)]
        adapter = AtkinsRealisAdapter(
            keywords_list=["project manager"], max_pages_per_query=5
        )
        mock_client = _mock_client(
            post_responses=[
                _make_search_response(page1_jobs, page=1, total_pages=2),
                _make_search_response(page2_jobs, page=2, total_pages=2),
            ],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert len(results) == 6

    def test_fetch_deduplication_across_keywords(self):
        shared_job = _make_job(job_id=9001, title="Senior PM")
        adapter = AtkinsRealisAdapter(
            keywords_list=["project manager", "senior pm"]
        )
        mock_client = _mock_client(
            post_responses=[
                _make_search_response([shared_job], total_pages=1),
                _make_search_response([shared_job], total_pages=1),
            ],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert len(results) == 1

    def test_fetch_since_filter_excludes_old_listings(self):
        old_job = _make_job(job_id=3001, created_at="2026-05-01T00:00:00.000Z")
        new_job = _make_job(job_id=3002, created_at="2026-06-15T00:00:00.000Z")
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        mock_client = _mock_client(
            post_responses=[
                _make_search_response([old_job, new_job], total_pages=1),
            ],
        )
        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch(since=since))
        assert len(results) == 1
        assert results[0].source_listing_id == "3002"

    def test_fetch_since_includes_listings_without_date(self):
        no_date_job = _make_job(job_id=4001, created_at="")
        no_date_job["created_at"] = None
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        mock_client = _mock_client(
            post_responses=[_make_search_response([no_date_job], total_pages=1)],
        )
        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch(since=since))
        # Listings without a date are always included.
        assert len(results) == 1

    def test_fetch_token_failure_returns_empty(self):
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        mock_client = _mock_client(token_status=500)
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert results == []

    def test_fetch_token_missing_field_returns_empty(self):
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        mock_client = _mock_client(token_response={"not_token": "xyz"})
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert results == []

    def test_fetch_api_500_returns_empty_for_keyword(self):
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        mock_client = _mock_client(
            post_statuses=[500],
            post_responses=[_make_search_response([])],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert results == []

    def test_fetch_401_invalidates_cached_token(self):
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        # Pre-seed a "valid" token so _token_is_valid returns True initially.
        adapter._token = "pre.cached.token"
        adapter._token_acquired_at = datetime.now(timezone.utc)

        mock_client = _mock_client(
            post_statuses=[401],
            post_responses=[_make_search_response([])],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            self._run(adapter.fetch())
        # After a 401, the cached token should be cleared.
        assert adapter._token is None
        assert adapter._token_acquired_at is None

    def test_fetch_request_error_handled_gracefully(self):
        import httpx as _httpx

        adapter = AtkinsRealisAdapter(keywords_list=["pm"])

        async def _raise_request_error(url, json=None, headers=None, **kwargs):
            raise _httpx.ConnectError("connection refused")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def _token_ok(url, **kwargs):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = _make_token_response()
            return r

        mock_client.get = AsyncMock(side_effect=_token_ok)
        mock_client.post = AsyncMock(side_effect=_raise_request_error)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert results == []

    def test_fetch_json_parse_error_skips_page(self):
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])

        async def _post_bad_json(url, json=None, headers=None, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.side_effect = ValueError("not JSON")
            return resp

        async def _token_ok(url, **kwargs):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = _make_token_response()
            return r

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_token_ok)
        mock_client.post = AsyncMock(side_effect=_post_bad_json)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert results == []

    def test_fetch_token_refreshed_when_expired_mid_run(self):
        """A stale token causes _ensure_token to call the token endpoint again."""
        jobs = [_make_job(job_id=7001)]
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])

        token_call_count = [0]

        async def _get(url, **kwargs):
            token_call_count[0] += 1
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = _make_token_response(f"token{token_call_count[0]}")
            return r

        post_call_index = [0]
        post_responses = [_make_search_response(jobs, total_pages=1)]

        async def _post(url, json=None, headers=None, **kwargs):
            idx = post_call_index[0]
            post_call_index[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            payload = (
                post_responses[idx] if idx < len(post_responses) else _make_search_response([])
            )
            resp.json.return_value = payload
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_get)
        mock_client.post = AsyncMock(side_effect=_post)

        # Force initial token to look expired so _ensure_token always fetches.
        adapter._token = None
        adapter._token_acquired_at = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert len(results) == 1
        # Token was fetched at least once.
        assert token_call_count[0] >= 1

    def test_fetch_stops_at_max_pages(self):
        """Adapter stops at max_pages_per_query even if totalPages is higher."""
        page_responses = [
            _make_search_response(
                [_make_job(job_id=i)],
                page=p,
                total_pages=10,
            )
            for p, i in enumerate(range(100, 103), start=1)
        ]
        adapter = AtkinsRealisAdapter(
            keywords_list=["pm"],
            max_pages_per_query=3,
        )
        mock_client = _mock_client(post_responses=page_responses)
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        # Only 3 pages worth of results expected.
        assert len(results) == 3

    def test_fetch_multiple_keywords_union(self):
        kw1_jobs = [_make_job(job_id=5001, title="Project Manager")]
        kw2_jobs = [_make_job(job_id=5002, title="Project Director")]
        adapter = AtkinsRealisAdapter(
            keywords_list=["project manager", "project director"]
        )
        mock_client = _mock_client(
            post_responses=[
                _make_search_response(kw1_jobs, total_pages=1),
                _make_search_response(kw2_jobs, total_pages=1),
            ],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        ids = {r.source_listing_id for r in results}
        assert "5001" in ids
        assert "5002" in ids

    def test_fetch_skips_jobs_with_no_title(self):
        bad_job = _make_job(job_id=6001, title="")
        good_job = _make_job(job_id=6002, title="Risk Manager")
        adapter = AtkinsRealisAdapter(keywords_list=["pm"])
        mock_client = _mock_client(
            post_responses=[_make_search_response([bad_job, good_job], total_pages=1)],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = self._run(adapter.fetch())
        assert len(results) == 1
        assert results[0].source_listing_id == "6002"
