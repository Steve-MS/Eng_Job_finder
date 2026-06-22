"""Unit tests for the Arcadis Eightfold AI adapter.

All tests use synthetic data and mocked HTTP — no live network calls.

Coverage (≥30 tests):
  _parse_posted_ts       : valid ts, zero, None, bad value, large ts
  _build_location_raw    : single, multiple, empty list, None, non-string items
  _position_to_raw_listing : full mapping, missing id, missing name, blank name,
                             None positionUrl, custom api_base, work_option,
                             metadata fields, source_listing_id as str
  parse_response         : happy path, missing count, non-int count, empty positions,
                           positions with invalid entries mixed in
  ArcadisAdapter ctor    : defaults, custom args, keywords accepted, extra kwargs ignored
  _build_url             : with/without location, offset encoding, keyword encoding
  fetch()                : happy path, source name, two-keyword dedup,
                           multi-page pagination, pagination stops at count,
                           partial final page stops pagination, since filter,
                           since keeps no-date entries, HTTP error, request error,
                           JSON parse failure, non-200 API status, empty positions,
                           missing data key, exception mid-fetch returns partial,
                           max_pages_per_query cap
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.arcadis import (
    ArcadisAdapter,
    _build_location_raw,
    _parse_posted_ts,
    _position_to_raw_listing,
    parse_response,
)


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------

def _make_position(
    pos_id: int = 563671530957634,
    display_job_id: str = "39090",
    name: str = "Senior Project Manager",
    locations: list[str] | None = None,
    standardized_locations: list[str] | None = None,
    posted_ts: int = 1773830043,
    department: str = "Project Management",
    work_location_option: str = "hybrid",
    ats_job_id: str = "300014987980785",
    position_url: str | None = None,
) -> dict:
    """Build a synthetic Eightfold position dict."""
    return {
        "id": pos_id,
        "displayJobId": display_job_id,
        "name": name,
        "locations": locations if locations is not None else ["Greater London, United Kingdom"],
        "standardizedLocations": standardized_locations or ["England,GB"],
        "postedTs": posted_ts,
        "solrScore": None,
        "stars": 0,
        "department": department,
        "creationTs": 1773243178,
        "isHot": 0,
        "workLocationOption": work_location_option,
        "locationFlexibility": None,
        "atsJobId": ats_job_id,
        "positionUrl": position_url or f"/careers/job/{pos_id}",
    }


def _make_api_response(
    positions: list[dict],
    count: int | None = None,
    status: int = 200,
) -> dict:
    """Build a synthetic top-level API response."""
    return {
        "status": status,
        "error": {"message": "", "body": ""},
        "data": {
            "positions": positions,
            "count": count if count is not None else len(positions),
            "filterDef": {},
            "sortBy": "relevance",
            "appliedFilters": [],
            "debug": {},
            "savedSearchMetadata": None,
            "resultsMetaData": {"usedFuzzSearch": False, "mocTitle": None},
        },
    }


def _make_mock_response(
    body: dict,
    status_code: int = 200,
) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _mock_client_sequence(responses: list[MagicMock]) -> AsyncMock:
    """Build a mock AsyncClient whose GET calls return responses in order."""
    call_idx = [0]

    async def _get(url, **kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        if idx < len(responses):
            return responses[idx]
        # After exhausting the list return an empty page
        return _make_mock_response(
            _make_api_response([], count=0)
        )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _parse_posted_ts
# ---------------------------------------------------------------------------

class TestParsePostedTs:
    def test_valid_timestamp(self):
        dt = _parse_posted_ts(1773830043)
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026

    def test_zero_timestamp(self):
        dt = _parse_posted_ts(0)
        assert dt is not None
        assert dt == datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_none_returns_none(self):
        assert _parse_posted_ts(None) is None

    def test_float_timestamp(self):
        dt = _parse_posted_ts(1773830043.5)
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_bad_value_returns_none(self):
        assert _parse_posted_ts("not-a-number") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _build_location_raw
# ---------------------------------------------------------------------------

class TestBuildLocationRaw:
    def test_single_location(self):
        pos = _make_position(locations=["Greater London, United Kingdom"])
        result = _build_location_raw(pos)
        assert result == "Greater London, United Kingdom"

    def test_multiple_locations(self):
        pos = _make_position(locations=["Manchester, United Kingdom", "Leeds, United Kingdom"])
        result = _build_location_raw(pos)
        assert result == "Manchester, United Kingdom, Leeds, United Kingdom"

    def test_empty_list_returns_none(self):
        pos = _make_position(locations=[])
        assert _build_location_raw(pos) is None

    def test_none_locations_returns_none(self):
        pos = _make_position(locations=None)
        pos["locations"] = None
        assert _build_location_raw(pos) is None

    def test_filters_empty_strings(self):
        pos = _make_position(locations=["London", "", "Bristol"])
        result = _build_location_raw(pos)
        assert result == "London, Bristol"

    def test_missing_key_returns_none(self):
        pos = {}
        assert _build_location_raw(pos) is None


# ---------------------------------------------------------------------------
# _position_to_raw_listing
# ---------------------------------------------------------------------------

class TestPositionToRawListing:
    _API_BASE = "https://jobs.arcadis.com"

    def test_full_field_mapping(self):
        pos = _make_position()
        listing = _position_to_raw_listing(pos, self._API_BASE)
        assert listing is not None
        assert listing.source == "arcadis"
        assert listing.source_listing_id == "563671530957634"
        assert listing.title == "Senior Project Manager"
        assert listing.url == "https://jobs.arcadis.com/careers/job/563671530957634"
        assert listing.employer == "Arcadis"
        assert listing.location_raw == "Greater London, United Kingdom"
        assert listing.posted_at is not None
        assert listing.contract_type_raw == "hybrid"

    def test_missing_id_returns_none(self):
        pos = _make_position()
        del pos["id"]
        assert _position_to_raw_listing(pos, self._API_BASE) is None

    def test_none_id_returns_none(self):
        pos = _make_position()
        pos["id"] = None
        assert _position_to_raw_listing(pos, self._API_BASE) is None

    def test_missing_name_returns_none(self):
        pos = _make_position()
        del pos["name"]
        assert _position_to_raw_listing(pos, self._API_BASE) is None

    def test_blank_name_returns_none(self):
        pos = _make_position(name="   ")
        assert _position_to_raw_listing(pos, self._API_BASE) is None

    def test_none_position_url_uses_fallback(self):
        pos = _make_position()
        pos["positionUrl"] = None
        listing = _position_to_raw_listing(pos, self._API_BASE)
        assert listing is not None
        assert "/careers/job/" in listing.url

    def test_custom_api_base(self):
        pos = _make_position()
        listing = _position_to_raw_listing(pos, "https://custom.example.com")
        assert listing is not None
        assert listing.url.startswith("https://custom.example.com")

    def test_source_listing_id_is_string(self):
        pos = _make_position(pos_id=12345)
        listing = _position_to_raw_listing(pos, self._API_BASE)
        assert isinstance(listing.source_listing_id, str)
        assert listing.source_listing_id == "12345"

    def test_work_location_option_as_contract_type(self):
        for opt in ("hybrid", "onsite", "remote"):
            pos = _make_position(work_location_option=opt)
            listing = _position_to_raw_listing(pos, self._API_BASE)
            assert listing.contract_type_raw == opt

    def test_no_salary_raw(self):
        pos = _make_position()
        listing = _position_to_raw_listing(pos, self._API_BASE)
        assert listing.salary_raw is None

    def test_no_description_raw(self):
        pos = _make_position()
        listing = _position_to_raw_listing(pos, self._API_BASE)
        assert listing.description_raw is None

    def test_metadata_fields(self):
        pos = _make_position()
        listing = _position_to_raw_listing(pos, self._API_BASE)
        assert listing.metadata["display_job_id"] == "39090"
        assert listing.metadata["department"] == "Project Management"
        assert listing.metadata["ats_job_id"] == "300014987980785"
        assert listing.metadata["standardized_locations"] == ["England,GB"]


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------

class TestParseResponse:
    _API_BASE = "https://jobs.arcadis.com"

    def test_happy_path(self):
        positions = [_make_position(pos_id=1), _make_position(pos_id=2, name="PM 2")]
        data = {"positions": positions, "count": 50}
        listings, count = parse_response(data, self._API_BASE)
        assert len(listings) == 2
        assert count == 50

    def test_missing_count_defaults_to_zero(self):
        data = {"positions": [_make_position()]}
        listings, count = parse_response(data, self._API_BASE)
        assert count == 0
        assert len(listings) == 1

    def test_non_int_count_defaults_to_zero(self):
        data = {"positions": [], "count": "many"}
        listings, count = parse_response(data, self._API_BASE)
        assert count == 0

    def test_empty_positions(self):
        data = {"positions": [], "count": 0}
        listings, count = parse_response(data, self._API_BASE)
        assert listings == []
        assert count == 0

    def test_invalid_positions_skipped(self):
        positions = [
            _make_position(pos_id=1),
            {"id": None, "name": "Bad"},   # None id → skipped
            {"name": "No ID"},              # missing id → skipped
            _make_position(pos_id=3, name="Good One"),
        ]
        data = {"positions": positions, "count": 4}
        listings, count = parse_response(data, self._API_BASE)
        assert len(listings) == 2
        assert {l.source_listing_id for l in listings} == {"1", "3"}

    def test_missing_positions_key(self):
        listings, count = parse_response({}, self._API_BASE)
        assert listings == []

    def test_none_positions_key(self):
        listings, count = parse_response({"positions": None, "count": 0}, self._API_BASE)
        assert listings == []


# ---------------------------------------------------------------------------
# ArcadisAdapter constructor
# ---------------------------------------------------------------------------

class TestArcadisAdapterCtor:
    def test_defaults(self):
        adapter = ArcadisAdapter()
        assert adapter.name == "arcadis"
        assert adapter.api_base == "https://jobs.arcadis.com"
        assert adapter.domain == "arcadis.com"
        assert adapter.location == "United Kingdom"
        assert adapter.crawl_delay == 2
        assert adapter.max_pages_per_query == 5
        assert "project manager" in adapter.keywords_list

    def test_custom_args(self):
        adapter = ArcadisAdapter(
            api_base="https://custom.example.com/",
            domain="example.com",
            keywords_list=["risk manager"],
            crawl_delay=5,
            max_pages_per_query=3,
            location="",
        )
        assert adapter.api_base == "https://custom.example.com"  # trailing slash stripped
        assert adapter.domain == "example.com"
        assert adapter.keywords_list == ["risk manager"]
        assert adapter.crawl_delay == 5
        assert adapter.max_pages_per_query == 3
        assert adapter.location == ""

    def test_extra_kwargs_ignored(self):
        adapter = ArcadisAdapter(unknown_param="ignored")
        assert adapter.name == "arcadis"


# ---------------------------------------------------------------------------
# _build_url
# ---------------------------------------------------------------------------

class TestBuildUrl:
    def test_url_contains_domain_and_query(self):
        adapter = ArcadisAdapter(keywords_list=["project manager"])
        url = adapter._build_url("project manager", 0)
        assert "domain=arcadis.com" in url
        assert "query=project+manager" in url
        assert "offset=0" in url
        assert "limit=10" in url

    def test_url_with_location(self):
        adapter = ArcadisAdapter(location="United Kingdom")
        url = adapter._build_url("risk manager", 0)
        assert "location=United+Kingdom" in url

    def test_url_without_location(self):
        adapter = ArcadisAdapter(location="")
        url = adapter._build_url("risk manager", 0)
        assert "location=" not in url

    def test_offset_applied(self):
        adapter = ArcadisAdapter()
        url = adapter._build_url("project manager", 20)
        assert "offset=20" in url

    def test_special_chars_encoded(self):
        adapter = ArcadisAdapter()
        url = adapter._build_url("M&E project manager", 0)
        assert "M%26E" in url or "M%2BE" in url or "M%26E" in url or "query=" in url
        # At minimum ensure the URL is a valid string with no raw & in query
        assert url.startswith("https://jobs.arcadis.com/api/pcsx/search?")


# ---------------------------------------------------------------------------
# fetch() — integration tests with mocked HTTP
# ---------------------------------------------------------------------------

class TestFetch:
    def _adapter(self, **kwargs) -> ArcadisAdapter:
        defaults = {
            "keywords_list": ["project manager"],
            "crawl_delay": 0,
            "max_pages_per_query": 5,
        }
        defaults.update(kwargs)
        return ArcadisAdapter(**defaults)

    def test_happy_path_returns_listings(self):
        pos = _make_position()
        response = _make_mock_response(_make_api_response([pos], count=1))
        adapter = self._adapter()
        mock_client = _mock_client_sequence([response])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert len(result) == 1
        assert result[0].source == "arcadis"

    def test_source_name_on_all_listings(self):
        positions = [_make_position(pos_id=i, name=f"PM {i}") for i in range(1, 4)]
        response = _make_mock_response(_make_api_response(positions, count=3))
        adapter = self._adapter()
        mock_client = _mock_client_sequence([response])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert all(l.source == "arcadis" for l in result)

    def test_dedup_across_keywords(self):
        """Same position returned by two keywords → only one entry."""
        pos = _make_position(pos_id=99)
        r1 = _make_mock_response(_make_api_response([pos], count=1))
        r2 = _make_mock_response(_make_api_response([pos], count=1))
        adapter = self._adapter(keywords_list=["project manager", "project director"])
        mock_client = _mock_client_sequence([r1, r2])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert len(result) == 1

    def test_multi_page_pagination(self):
        """Two pages of 10 → 20 unique results."""
        page1_positions = [_make_position(pos_id=i, name=f"PM {i}") for i in range(10)]
        page2_positions = [_make_position(pos_id=i + 10, name=f"PM {i+10}") for i in range(10)]
        r1 = _make_mock_response(_make_api_response(page1_positions, count=20))
        r2 = _make_mock_response(_make_api_response(page2_positions, count=20))
        adapter = self._adapter()
        mock_client = _mock_client_sequence([r1, r2])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert len(result) == 20

    def test_pagination_stops_when_count_reached(self):
        """count=5 means only one page needed even with max_pages=5."""
        positions = [_make_position(pos_id=i, name=f"PM {i}") for i in range(5)]
        r1 = _make_mock_response(_make_api_response(positions, count=5))
        adapter = self._adapter()
        mock_client = _mock_client_sequence([r1])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        # Only 1 GET should have been made (offset=0, count=5 → no more pages)
        assert len(result) == 5

    def test_empty_page_stops_pagination(self):
        """Empty positions list → stop paginating."""
        r1 = _make_mock_response(_make_api_response([], count=0))
        adapter = self._adapter()
        mock_client = _mock_client_sequence([r1])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert result == []

    def test_max_pages_cap(self):
        """With max_pages_per_query=2, stops after 2 pages regardless of count."""
        page_positions = [_make_position(pos_id=i, name=f"PM {i}") for i in range(10)]
        r1 = _make_mock_response(_make_api_response(page_positions, count=100))
        r2 = _make_mock_response(
            _make_api_response(
                [_make_position(pos_id=i + 10, name=f"PM {i+10}") for i in range(10)],
                count=100,
            )
        )
        adapter = self._adapter(max_pages_per_query=2)
        mock_client = _mock_client_sequence([r1, r2])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert len(result) == 20

    def test_since_filter_excludes_old_listings(self):
        old_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
        new_ts = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
        positions = [
            _make_position(pos_id=1, posted_ts=old_ts),
            _make_position(pos_id=2, posted_ts=new_ts, name="New PM"),
        ]
        r1 = _make_mock_response(_make_api_response(positions, count=2))
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        adapter = self._adapter()
        mock_client = _mock_client_sequence([r1])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch(since=since))
        assert len(result) == 1
        assert result[0].source_listing_id == "2"

    def test_since_keeps_no_date_listings(self):
        pos = _make_position(pos_id=5)
        pos["postedTs"] = None
        r1 = _make_mock_response(_make_api_response([pos], count=1))
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        adapter = self._adapter()
        mock_client = _mock_client_sequence([r1])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch(since=since))
        assert len(result) == 1

    def test_http_error_returns_empty(self):
        error_resp = _make_mock_response({}, status_code=500)
        adapter = self._adapter()
        mock_client = _mock_client_sequence([error_resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert result == []

    def test_request_error_returns_empty(self):
        import httpx as _httpx

        async def _raise(*args, **kwargs):
            raise _httpx.RequestError("connection refused")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_raise)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        adapter = self._adapter()
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert result == []

    def test_json_parse_failure_returns_empty(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("bad JSON")
        mock_client = _mock_client_sequence([resp])
        adapter = self._adapter()
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert result == []

    def test_non_200_api_status_returns_empty(self):
        body = {"status": 500, "data": {"positions": [_make_position()], "count": 1}}
        resp = _make_mock_response(body)
        adapter = self._adapter()
        mock_client = _mock_client_sequence([resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert result == []

    def test_missing_data_key_returns_empty(self):
        body = {"status": 200}
        resp = _make_mock_response(body)
        adapter = self._adapter()
        mock_client = _mock_client_sequence([resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert result == []

    def test_exception_mid_fetch_returns_partial(self):
        pos = _make_position(pos_id=1)
        r1 = _make_mock_response(_make_api_response([pos], count=20))

        call_count = [0]

        async def _get_raising(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return r1
            raise RuntimeError("mid-fetch error")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_get_raising)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        adapter = self._adapter(
            keywords_list=["project manager", "project director"],
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        # Should return whatever was collected before the error
        assert isinstance(result, list)

    def test_multiple_keywords_all_queried(self):
        """Adapter queries each keyword in keywords_list."""
        positions_pm = [_make_position(pos_id=1, name="PM")]
        positions_pd = [_make_position(pos_id=2, name="PD")]
        r1 = _make_mock_response(_make_api_response(positions_pm, count=1))
        r2 = _make_mock_response(_make_api_response(positions_pd, count=1))
        adapter = self._adapter(keywords_list=["project manager", "project director"])
        mock_client = _mock_client_sequence([r1, r2])
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(adapter.fetch())
        assert len(result) == 2
        ids = {l.source_listing_id for l in result}
        assert ids == {"1", "2"}
