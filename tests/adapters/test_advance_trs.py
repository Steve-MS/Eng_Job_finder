"""Unit tests for the Advance TRS adapter.

All tests use synthetic data and mocked HTTP — no live network calls.

Coverage:
  - _parse_posted_date: WP datetime, plain date, None, empty, garbage
  - _item_to_raw_listing: full field mapping, empty salary, missing fields,
    contract_type_raw from job-types, employer fallback
  - AdvanceTrsAdapter constructor and defaults
  - fetch(): single page (x-wp-totalpages: 1), two-page pagination,
    deduplication, since-filter, HTTP error, request error
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.advance_trs import (
    AdvanceTrsAdapter,
    _EMPLOYER,
    _JOB_TYPE_CONTRACT,
    _SOURCE_NAME,
    _item_to_raw_listing,
    _parse_posted_date,
)


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------

def _make_item(
    wp_id: int = 12345,
    title: str = "Rail Energy Manager",
    link: str = "https://www.advance-trs.com/job/rail-energy-manager/",
    date: str = "2026-06-15T10:00:00",
    location: str = "East Riding of Yorkshire",
    salary: str = "",
    company: str = "Advance Training and Recruitment Services",
    job_expires: str = "2026-07-15",
    application: str = "email@aplitrak.com",
    description_html: str = "<p>Full HTML description of the role.</p>",
    job_types: list[int] | None = None,
) -> dict:
    """Build a synthetic WP Job Manager job-listings API response item."""
    if job_types is None:
        job_types = [_JOB_TYPE_CONTRACT]
    return {
        "id": wp_id,
        "date": date,
        "title": {"rendered": title},
        "link": link,
        "content": {"rendered": description_html},
        "excerpt": {"rendered": f"<p>Excerpt for {title}.</p>"},
        "meta": {
            "_job_location": location,
            "_job_salary": salary,
            "_company_name": company,
            "_job_expires": job_expires,
            "_application": application,
        },
        "job-categories": [549],
        "job-types": job_types,
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
        # Default: empty page 200
        return _make_mock_response([], total=0, total_pages=1)

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=_get)
    return mock


# ===========================================================================
# _parse_posted_date
# ===========================================================================


class TestParsePostedDate:
    def test_wp_full_datetime(self):
        dt = _parse_posted_date("2026-06-15T10:00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 15
        assert dt.hour == 10
        assert dt.tzinfo == timezone.utc

    def test_plain_date(self):
        dt = _parse_posted_date("2026-01-20")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 1
        assert dt.day == 20
        assert dt.tzinfo == timezone.utc

    def test_date_embedded_in_longer_string(self):
        # Only the first 10 chars are used for plain-date fallback
        dt = _parse_posted_date("2026-03-05T00:00")
        assert dt is not None
        assert dt.month == 3
        assert dt.day == 5

    def test_none_returns_none(self):
        assert _parse_posted_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_posted_date("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_posted_date("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_posted_date("not-a-date") is None

    def test_midnight_datetime(self):
        dt = _parse_posted_date("2026-06-01T00:00:00")
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
        assert listing.source_listing_id == "12345"
        assert listing.title == "Rail Energy Manager"
        assert listing.url == "https://www.advance-trs.com/job/rail-energy-manager/"
        assert listing.location_raw == "East Riding of Yorkshire"
        assert listing.employer == "Advance Training and Recruitment Services"
        assert listing.agency is None

    def test_posted_at_parsed(self):
        item = _make_item(date="2026-06-15T10:00:00")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.posted_at is not None
        assert listing.posted_at.year == 2026
        assert listing.posted_at.month == 6
        assert listing.posted_at.day == 15

    def test_description_raw_from_content_rendered(self):
        html = "<p>Full HTML description of the role.</p>"
        item = _make_item(description_html=html)
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.description_raw == html

    def test_salary_empty_string_becomes_none(self):
        item = _make_item(salary="")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.salary_raw is None

    def test_salary_whitespace_only_becomes_none(self):
        item = _make_item(salary="   ")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.salary_raw is None

    def test_salary_present_is_preserved(self):
        item = _make_item(salary="£400 - £450 per day")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.salary_raw == "£400 - £450 per day"

    def test_contract_type_raw_set_for_contract_job(self):
        item = _make_item(job_types=[_JOB_TYPE_CONTRACT])
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.contract_type_raw == "Contract"

    def test_contract_type_raw_none_when_no_contract_type(self):
        item = _make_item(job_types=[8])  # Permanent
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.contract_type_raw is None

    def test_contract_type_raw_none_when_empty_types(self):
        item = _make_item(job_types=[])
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.contract_type_raw is None

    def test_employer_fallback_when_company_name_empty(self):
        item = _make_item(company="")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.employer == _EMPLOYER

    def test_employer_from_meta_when_present(self):
        item = _make_item(company="Custom Employer Ltd")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.employer == "Custom Employer Ltd"

    def test_missing_id_returns_none(self):
        item = _make_item()
        del item["id"]
        assert _item_to_raw_listing(item) is None

    def test_none_id_returns_none(self):
        item = _make_item()
        item["id"] = None
        assert _item_to_raw_listing(item) is None

    def test_missing_title_returns_none(self):
        item = _make_item()
        item["title"] = {"rendered": ""}
        assert _item_to_raw_listing(item) is None

    def test_missing_link_returns_none(self):
        item = _make_item()
        item["link"] = ""
        assert _item_to_raw_listing(item) is None

    def test_empty_location_becomes_none(self):
        item = _make_item(location="")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.location_raw is None

    def test_empty_description_becomes_none(self):
        item = _make_item(description_html="")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.description_raw is None

    def test_metadata_contains_wp_id(self):
        item = _make_item(wp_id=99001)
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.metadata["wp_id"] == 99001

    def test_metadata_contains_job_expires(self):
        item = _make_item(job_expires="2026-07-31")
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert listing.metadata["job_expires"] == "2026-07-31"

    def test_source_listing_id_is_string(self):
        item = _make_item(wp_id=42)
        listing = _item_to_raw_listing(item)
        assert listing is not None
        assert isinstance(listing.source_listing_id, str)
        assert listing.source_listing_id == "42"


# ===========================================================================
# AdvanceTrsAdapter constructor
# ===========================================================================


class TestAdvanceTrsAdapterConstructor:
    def test_default_values(self):
        adapter = AdvanceTrsAdapter()
        assert adapter.name == _SOURCE_NAME
        assert adapter.crawl_delay == 3
        assert adapter.per_page == 100

    def test_custom_crawl_delay(self):
        adapter = AdvanceTrsAdapter(crawl_delay=10)
        assert adapter.crawl_delay == 10

    def test_custom_per_page(self):
        adapter = AdvanceTrsAdapter(per_page=50)
        assert adapter.per_page == 50

    def test_extra_kwargs_ignored(self):
        # CLI passes keywords_list from config even though this adapter
        # doesn't use it — must not raise
        adapter = AdvanceTrsAdapter(keywords_list=["project manager"])
        assert adapter.name == _SOURCE_NAME


# ===========================================================================
# AdvanceTrsAdapter.fetch()
# ===========================================================================


class TestAdvanceTrsAdapterFetch:
    """fetch() tests with mocked httpx.AsyncClient."""

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_single_page_returns_listings(self, mock_client_cls, mock_sleep):
        items = [_make_item(wp_id=1), _make_item(wp_id=2, title="Senior PM")]
        resp = _make_mock_response(items, total=2, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 2
        ids = {l.source_listing_id for l in listings}
        assert ids == {"1", "2"}

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_two_page_pagination(self, mock_client_cls, mock_sleep):
        page1_items = [_make_item(wp_id=i) for i in range(1, 4)]
        page2_items = [_make_item(wp_id=i) for i in range(4, 6)]
        resp1 = _make_mock_response(page1_items, total=5, total_pages=2)
        resp2 = _make_mock_response(page2_items, total=5, total_pages=2)
        mock_client_cls.return_value = _mock_client([resp1, resp2])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 5
        ids = {l.source_listing_id for l in listings}
        assert ids == {"1", "2", "3", "4", "5"}

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_deduplication_same_id_on_two_pages(self, mock_client_cls, mock_sleep):
        shared_item = _make_item(wp_id=999, title="Duplicate Role")
        resp1 = _make_mock_response([shared_item], total=1, total_pages=2)
        resp2 = _make_mock_response([shared_item], total=1, total_pages=2)
        mock_client_cls.return_value = _mock_client([resp1, resp2])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 1
        assert listings[0].source_listing_id == "999"

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_since_filter_excludes_old_listings(self, mock_client_cls, mock_sleep):
        old = _make_item(wp_id=1, date="2026-05-01T00:00:00")
        new = _make_item(wp_id=2, date="2026-06-15T00:00:00")
        resp = _make_mock_response([old, new], total=2, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch(since=since))

        assert len(listings) == 1
        assert listings[0].source_listing_id == "2"

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_since_filter_keeps_no_date_entries(self, mock_client_cls, mock_sleep):
        """Listings without posted_at are always included regardless of since."""
        item = _make_item(wp_id=5, date="")
        resp = _make_mock_response([item], total=1, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch(since=since))

        assert len(listings) == 1

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_http_error_returns_empty_list(self, mock_client_cls, mock_sleep):
        resp = _make_mock_response([], status_code=503, total=0, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
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

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_empty_page_body_is_handled(self, mock_client_cls, mock_sleep):
        resp = _make_mock_response([], total=0, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_salary_empty_in_listing_is_none(self, mock_client_cls, mock_sleep):
        item = _make_item(wp_id=10, salary="")
        resp = _make_mock_response([item], total=1, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 1
        assert listings[0].salary_raw is None

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_contract_type_from_job_types_header(self, mock_client_cls, mock_sleep):
        """All fetched items have job-types=[7] so contract_type_raw is 'Contract'."""
        item = _make_item(wp_id=20, job_types=[_JOB_TYPE_CONTRACT])
        resp = _make_mock_response([item], total=1, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 1
        assert listings[0].contract_type_raw == "Contract"

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_non_json_response_returns_empty(self, mock_client_cls, mock_sleep):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"x-wp-total": "0", "x-wp-totalpages": "1"}
        resp.json.side_effect = ValueError("not json")
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_missing_totalpages_header_defaults_to_one(self, mock_client_cls, mock_sleep):
        """If x-wp-totalpages is absent, adapter fetches only one page."""
        items = [_make_item(wp_id=7)]
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}  # no pagination headers
        resp.json.return_value = items
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 1

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_source_name_is_advance_trs(self, mock_client_cls, mock_sleep):
        item = _make_item(wp_id=100)
        resp = _make_mock_response([item], total=1, total_pages=1)
        mock_client_cls.return_value = _mock_client([resp])

        adapter = AdvanceTrsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert all(l.source == _SOURCE_NAME for l in listings)

    @patch("mechpm.adapters.advance_trs.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    def test_page_delay_called_between_pages(self, mock_client_cls, _mock_sleep):
        """asyncio.sleep is called once between page 1 and page 2."""
        sleep_mock = AsyncMock(return_value=None)
        page1 = _make_mock_response([_make_item(wp_id=1)], total=2, total_pages=2)
        page2 = _make_mock_response([_make_item(wp_id=2)], total=2, total_pages=2)
        mock_client_cls.return_value = _mock_client([page1, page2])

        adapter = AdvanceTrsAdapter()
        with patch("mechpm.adapters.advance_trs.asyncio.sleep", sleep_mock):
            asyncio.run(adapter.fetch())

        sleep_mock.assert_awaited_once()
