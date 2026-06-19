"""Unit tests for the CIOB Jobs WP REST API adapter.

All tests use synthetic data and mocked HTTP — no live network calls.

Coverage (54 tests):
  _strip_html            : tag removal, plain-text passthrough, nested tags, empty
  _extract_field         : location/salary/employer match, no match
  _parse_wp_date         : full datetime, plain date, short format, None, empty,
                           whitespace, garbage, midnight
  _item_to_raw_listing   : full field mapping, id/title/link guards, empty fields,
                           location/salary/employer extraction, no extractions,
                           metadata (wp_id, industry_sector, job_sector, job_specialism),
                           source_listing_id as string, contract_type_raw is None
  CiobJobsAdapter ctor   : defaults (name, crawl_delay, api_base, per_page, max_pages),
                           keywords_list accepted, extra kwargs ignored, custom api_base
  fetch()                : happy path, source name, two-page pagination, max_pages cap,
                           dedup across keywords, dedup across pages, since filter,
                           since keeps no-date entries, HTTP error, request error,
                           JSON parse failure, non-list body, empty results,
                           missing totalpages header, multiple keywords all queried
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.ciob_jobs import (
    CiobJobsAdapter,
    _RE_EMPLOYER,
    _RE_LOCATION,
    _RE_SALARY,
    _SOURCE_NAME,
    _extract_field,
    _item_to_raw_listing,
    _parse_wp_date,
    _strip_html,
)


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------


def _make_item(
    wp_id: int = 132073,
    title: str = "Project Civil Engineer",
    link: str = "https://ciobjobs.com/job/132073/project-civil-engineer/",
    date: str = "2026-06-19T09:35:51",
    description_html: str = "<p>Location: London</p><p>Salary: £500/day</p><p>Employer: Build Corp Ltd</p>",
    industry_sector: list[int] | None = None,
    job_sector: list[int] | None = None,
    job_specialism: list[int] | None = None,
) -> dict:
    """Build a synthetic WP REST API job response item."""
    return {
        "id": wp_id,
        "date": date,
        "date_gmt": date,
        "slug": title.lower().replace(" ", "-"),
        "link": link,
        "title": {"rendered": title},
        "content": {"rendered": description_html},
        "industry-sector": industry_sector or [96],
        "job-sector": job_sector or [431],
        "job-specialism": job_specialism or [341],
    }


def _make_mock_response(
    items: list[dict],
    status_code: int = 200,
    total: int | None = None,
    total_pages: int = 1,
) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = items
    resp.headers = {
        "x-wp-total": str(total if total is not None else len(items)),
        "x-wp-totalpages": str(total_pages),
        "content-type": "application/json; charset=UTF-8",
    }
    return resp


def _mock_client(page_responses: list[MagicMock]) -> AsyncMock:
    """Build a mock httpx.AsyncClient whose GET calls return page_responses in order."""
    call_index = [0]

    async def _get(url, **kwargs):
        idx = call_index[0]
        call_index[0] += 1
        if idx < len(page_responses):
            return page_responses[idx]
        return _make_mock_response([], total=0, total_pages=1)

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=_get)
    return mock


# ===========================================================================
# _strip_html
# ===========================================================================


class TestStripHtml:
    def test_removes_paragraph_tags(self):
        assert "Hello World" in _strip_html("<p>Hello World</p>")

    def test_plain_text_unchanged(self):
        result = _strip_html("No HTML here")
        assert "No HTML here" in result

    def test_strips_nested_tags(self):
        html = "<strong><em>Bold italic</em></strong>"
        assert "Bold italic" in _strip_html(html)

    def test_empty_string_returns_empty(self):
        assert _strip_html("") == ""


# ===========================================================================
# _extract_field
# ===========================================================================


class TestExtractField:
    def test_location_match(self):
        result = _extract_field(_RE_LOCATION, "Location: Manchester, UK")
        assert result is not None
        assert "Manchester" in result

    def test_salary_match(self):
        result = _extract_field(_RE_SALARY, "Salary: £50,000 - £60,000 per annum")
        assert result is not None
        assert "£50,000" in result

    def test_employer_match(self):
        result = _extract_field(_RE_EMPLOYER, "Employer: ABC Construction Ltd")
        assert result is not None
        assert "ABC Construction" in result

    def test_no_match_returns_none(self):
        assert _extract_field(_RE_LOCATION, "No location here") is None


# ===========================================================================
# _parse_wp_date
# ===========================================================================


class TestParseWpDate:
    def test_full_wp_datetime(self):
        dt = _parse_wp_date("2026-06-19T09:35:51")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 19
        assert dt.hour == 9
        assert dt.minute == 35
        assert dt.tzinfo == timezone.utc

    def test_plain_date(self):
        dt = _parse_wp_date("2026-01-20")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 1
        assert dt.day == 20
        assert dt.tzinfo == timezone.utc

    def test_short_hhmm_format(self):
        dt = _parse_wp_date("2026-03-05T14:30")
        assert dt is not None
        assert dt.month == 3
        assert dt.day == 5
        assert dt.hour == 14

    def test_none_returns_none(self):
        assert _parse_wp_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_wp_date("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_wp_date("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_wp_date("not-a-date") is None

    def test_midnight_datetime(self):
        dt = _parse_wp_date("2026-06-01T00:00:00")
        assert dt is not None
        assert dt.hour == 0
        assert dt.minute == 0


# ===========================================================================
# _item_to_raw_listing
# ===========================================================================


class TestItemToRawListing:
    def test_basic_field_mapping(self):
        item = _make_item()
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.source == _SOURCE_NAME
        assert listing.title == "Project Civil Engineer"
        assert listing.url == "https://ciobjobs.com/job/132073/project-civil-engineer/"
        assert listing.agency is None

    def test_source_listing_id_is_string_of_wp_id(self):
        item = _make_item(wp_id=42)
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert isinstance(listing.source_listing_id, str)
        assert listing.source_listing_id == "42"

    def test_posted_at_parsed_from_date(self):
        item = _make_item(date="2026-06-19T09:35:51")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.posted_at is not None
        assert listing.posted_at.year == 2026
        assert listing.posted_at.month == 6
        assert listing.posted_at.day == 19

    def test_description_raw_from_content_rendered(self):
        html = "<p>Full HTML job description.</p>"
        item = _make_item(description_html=html)
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.description_raw == html

    def test_empty_description_becomes_none(self):
        item = _make_item(description_html="")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.description_raw is None

    def test_location_extracted_from_html_content(self):
        item = _make_item(description_html="<p>Location: Birmingham</p>")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.location_raw is not None
        assert "Birmingham" in listing.location_raw

    def test_salary_extracted_from_html_content(self):
        item = _make_item(description_html="<p>Salary: £400 - £450 per day</p>")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.salary_raw is not None
        assert "£400" in listing.salary_raw

    def test_employer_extracted_from_html_content(self):
        item = _make_item(description_html="<p>Employer: Construct Corp Ltd</p>")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.employer is not None
        assert "Construct Corp" in listing.employer

    def test_no_structured_fields_gives_none_extractions(self):
        item = _make_item(description_html="<p>Generic description with no labels.</p>")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.location_raw is None
        assert listing.salary_raw is None
        assert listing.employer is None

    def test_missing_id_returns_none(self):
        item = _make_item()
        del item["id"]
        assert _item_to_raw_listing(item) is None

    def test_none_id_returns_none(self):
        item = _make_item()
        item["id"] = None
        assert _item_to_raw_listing(item) is None

    def test_zero_id_returns_none(self):
        item = _make_item()
        item["id"] = 0
        assert _item_to_raw_listing(item) is None

    def test_missing_title_returns_none(self):
        item = _make_item()
        item["title"] = {"rendered": ""}
        assert _item_to_raw_listing(item) is None

    def test_missing_link_returns_none(self):
        item = _make_item()
        item["link"] = ""
        assert _item_to_raw_listing(item) is None

    def test_contract_type_raw_is_none(self):
        # CIOB API has no contract-type taxonomy; always None
        item = _make_item()
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.contract_type_raw is None

    def test_metadata_contains_wp_id(self):
        item = _make_item(wp_id=99001)
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.metadata["wp_id"] == 99001

    def test_metadata_contains_industry_sector(self):
        item = _make_item(industry_sector=[96, 97])
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.metadata["industry_sector"] == [96, 97]

    def test_metadata_contains_job_sector(self):
        item = _make_item(job_sector=[431])
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.metadata["job_sector"] == [431]

    def test_metadata_contains_job_specialism(self):
        item = _make_item(job_specialism=[341, 342])
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.metadata["job_specialism"] == [341, 342]


# ===========================================================================
# CiobJobsAdapter constructor
# ===========================================================================


class TestCiobJobsAdapterConstructor:
    def test_default_name(self):
        assert CiobJobsAdapter.name == _SOURCE_NAME

    def test_default_crawl_delay(self):
        adapter = CiobJobsAdapter()
        assert adapter.crawl_delay == 2

    def test_default_api_base(self):
        adapter = CiobJobsAdapter()
        assert adapter.api_base == "https://ciobjobs.com"

    def test_default_per_page(self):
        adapter = CiobJobsAdapter()
        assert adapter.per_page == 100

    def test_default_max_pages(self):
        adapter = CiobJobsAdapter()
        assert adapter.max_pages == 5

    def test_keywords_list_accepted(self):
        adapter = CiobJobsAdapter(keywords_list=["project manager", "risk manager"])
        assert adapter.keywords_list == ["project manager", "risk manager"]

    def test_none_keywords_list_becomes_empty(self):
        adapter = CiobJobsAdapter(keywords_list=None)
        assert adapter.keywords_list == []

    def test_custom_api_base_trailing_slash_stripped(self):
        adapter = CiobJobsAdapter(api_base="https://example.com/")
        assert adapter.api_base == "https://example.com"

    def test_extra_kwargs_ignored(self):
        adapter = CiobJobsAdapter(unknown_param="ignored")
        assert adapter.name == _SOURCE_NAME


# ===========================================================================
# CiobJobsAdapter.fetch()  (mocked HTTP)
# ===========================================================================


class TestCiobJobsAdapterFetch:
    """fetch() tests with mocked httpx.AsyncClient."""

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_happy_path_single_keyword_single_page(self, mock_client_cls, mock_sleep):
        items = [_make_item(wp_id=1), _make_item(wp_id=2, title="Senior PM")]
        resp = _make_mock_response(items, total=2, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 2
        ids = {l.source_listing_id for l in listings}
        assert ids == {"1", "2"}

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_source_name_is_ciob_jobs(self, mock_client_cls, mock_sleep):
        resp = _make_mock_response([_make_item(wp_id=10)], total=1, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert all(l.source == _SOURCE_NAME for l in listings)

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_two_page_pagination_single_keyword(self, mock_client_cls, mock_sleep):
        page1 = [_make_item(wp_id=i) for i in range(1, 4)]
        page2 = [_make_item(wp_id=i) for i in range(4, 6)]
        resp1 = _make_mock_response(page1, total=5, total_pages=2)
        resp2 = _make_mock_response(page2, total=5, total_pages=2)
        mock_client_cls.return_value = _mock_client([resp1, resp2])

        adapter = CiobJobsAdapter(keywords_list=["project manager"], max_pages=5)
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 5
        assert {l.source_listing_id for l in listings} == {"1", "2", "3", "4", "5"}

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_max_pages_cap_stops_pagination(self, mock_client_cls, mock_sleep):
        # API reports 10 pages but max_pages=2 — only 2 GET calls expected
        call_count = [0]

        async def _get(url, **kwargs):
            call_count[0] += 1
            items = [_make_item(wp_id=call_count[0] * 100)]
            return _make_mock_response(items, total=1000, total_pages=10)

        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        mock.get = AsyncMock(side_effect=_get)
        mock_client_cls.return_value = mock

        adapter = CiobJobsAdapter(keywords_list=["project manager"], max_pages=2)
        asyncio.run(adapter.fetch())

        assert call_count[0] == 2

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_dedup_across_two_keywords(self, mock_client_cls, mock_sleep):
        shared = _make_item(wp_id=999, title="Shared Role")
        unique_kw1 = _make_item(wp_id=1, title="Only KW1")
        unique_kw2 = _make_item(wp_id=2, title="Only KW2")
        resp_kw1 = _make_mock_response([shared, unique_kw1], total=2, total_pages=1)
        resp_kw2 = _make_mock_response([shared, unique_kw2], total=2, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp_kw1, resp_kw2])

        adapter = CiobJobsAdapter(keywords_list=["project manager", "risk manager"])
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 3
        ids = {l.source_listing_id for l in listings}
        assert ids == {"999", "1", "2"}

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_dedup_across_pages_same_keyword(self, mock_client_cls, mock_sleep):
        shared = _make_item(wp_id=999, title="Duplicate Role")
        resp1 = _make_mock_response([shared], total=1, total_pages=2)
        resp2 = _make_mock_response([shared], total=1, total_pages=2)
        mock_client_cls.return_value = _mock_client([resp1, resp2])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 1
        assert listings[0].source_listing_id == "999"

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_since_filter_excludes_old_listings(self, mock_client_cls, mock_sleep):
        old = _make_item(wp_id=1, date="2026-05-01T00:00:00")
        new = _make_item(wp_id=2, date="2026-06-15T00:00:00")
        resp = _make_mock_response([old, new], total=2, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch(since=since))

        assert len(listings) == 1
        assert listings[0].source_listing_id == "2"

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_since_filter_keeps_no_date_entries(self, mock_client_cls, mock_sleep):
        item = _make_item(wp_id=5, date="")
        resp = _make_mock_response([item], total=1, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch(since=since))

        assert len(listings) == 1

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_http_error_returns_empty_list(self, mock_client_cls, mock_sleep):
        resp = _make_mock_response([], status_code=503, total=0, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_request_error_returns_empty_list(self, mock_client_cls, mock_sleep):
        import httpx as _httpx

        async def _get_raises(url, **kwargs):
            raise _httpx.RequestError("connection refused")

        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        mock.get = AsyncMock(side_effect=_get_raises)
        mock_client_cls.return_value = mock

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_json_parse_failure_returns_empty_list(self, mock_client_cls, mock_sleep):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"x-wp-totalpages": "1"}
        resp.json.side_effect = ValueError("not json")
        mock_client_cls.return_value = _mock_client([resp])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_non_list_response_body_returns_empty_list(self, mock_client_cls, mock_sleep):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"x-wp-totalpages": "1"}
        resp.json.return_value = {"error": "unexpected dict"}
        mock_client_cls.return_value = _mock_client([resp])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_empty_results_returns_empty_list(self, mock_client_cls, mock_sleep):
        resp = _make_mock_response([], total=0, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_missing_totalpages_header_defaults_to_one_page(self, mock_client_cls, mock_sleep):
        items = [_make_item(wp_id=7)]
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}  # no pagination headers
        resp.json.return_value = items
        mock_client_cls.return_value = _mock_client([resp])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 1

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_multiple_keywords_both_queried(self, mock_client_cls, mock_sleep):
        get_calls = []

        async def _get(url, **kwargs):
            get_calls.append(url)
            wp_id = len(get_calls) * 100
            items = [_make_item(wp_id=wp_id)]
            return _make_mock_response(items, total=1, total_pages=1)

        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        mock.get = AsyncMock(side_effect=_get)
        mock_client_cls.return_value = mock

        adapter = CiobJobsAdapter(keywords_list=["project manager", "risk manager"])
        asyncio.run(adapter.fetch())

        assert len(get_calls) == 2
        assert any("project+manager" in url for url in get_calls)
        assert any("risk+manager" in url for url in get_calls)

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_no_keywords_returns_empty_list(self, mock_client_cls, mock_sleep):
        mock_client_cls.return_value = _mock_client([])

        adapter = CiobJobsAdapter(keywords_list=[])
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.ciob_jobs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_page_delay_called_between_pages(self, mock_client_cls, _mock_sleep):
        """asyncio.sleep is called once between page 1 and page 2."""
        sleep_mock = AsyncMock(return_value=None)
        page1 = _make_mock_response([_make_item(wp_id=1)], total=2, total_pages=2)
        page2 = _make_mock_response([_make_item(wp_id=2)], total=2, total_pages=2)
        mock_client_cls.return_value = _mock_client([page1, page2])

        adapter = CiobJobsAdapter(keywords_list=["project manager"])
        with patch("mechpm.adapters.ciob_jobs.asyncio.sleep", sleep_mock):
            asyncio.run(adapter.fetch())

        sleep_mock.assert_awaited_once()

