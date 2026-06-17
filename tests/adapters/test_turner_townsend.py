"""Unit tests for the Turner & Townsend adapter.

All tests use synthetic data and mocked HTTP — no live network calls.

Coverage:
  - Pure helper functions (_extract_listing_id, _parse_posted_date, etc.)
  - Field mapping (_content_item_to_raw_listing)
  - Detail enrichment (_apply_detail)
  - Adapter constructor and defaults
  - fetch(): single page, pagination, dedup, enrichment, error handling
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.turner_townsend import (
    TurnerTownsendAdapter,
    _apply_detail,
    _build_location_raw,
    _content_item_to_raw_listing,
    _extract_listing_id,
    _get_custom_field,
    _parse_posted_date,
    _SOURCE_NAME,
    _EMPLOYER,
)


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------

_REF_BASE = "https://api.smartrecruiters.com/v1/companies/TurnerTownsend/postings/"


def _make_item(
    name: str = "Consultant Project Manager - Construction",
    listing_id: str = "744000131433769",
    released_date: str = "2026-06-10T11:50:27.291Z",
    city: str = "Sheffield",
    country: str = "United Kingdom",
    department_label: str = "Real estate",
    discipline: str = "Project Management",
) -> dict:
    """Build a synthetic listingModel.content entry."""
    return {
        "name": name,
        "ref": f"{_REF_BASE}{listing_id}",
        "releasedDate": released_date,
        "function": {"id": "consulting", "label": "Consulting"},
        "department": {"id": "1360908", "label": department_label},
        "location": {"city": city, "country": country},
        "customField": [
            {
                "fieldId": "5d36d00198f1310d2034cf62",
                "fieldLabel": "Brands",
                "valueLabel": "Turner & Townsend",
            },
            {
                "fieldId": "5d36d00198f1310d2034cf63",
                "fieldLabel": "Department",
                "valueLabel": department_label,
            },
            {
                "fieldId": "5d7a57d5b9a21b783becfb78",
                "fieldLabel": "Region",
                "valueLabel": "United Kingdom",
            },
            {
                "fieldId": "COUNTRY",
                "fieldLabel": "Country/Region",
                "valueId": "gb",
                "valueLabel": "United Kingdom",
            },
            {
                "fieldId": "5e7b88a676f9586f1ea45b74",
                "fieldLabel": "Discipline",
                "valueLabel": discipline,
            },
        ],
    }


def _make_search_response(
    items: list[dict],
    page: int = 1,
    total_pages: int = 1,
) -> dict:
    """Build a synthetic searchvacancies API response."""
    total_items = len(items)
    return {
        "listingModel": {
            "totalFound": total_items,
            "content": items,
        },
        "paginationModel": {
            "totalPages": total_pages,
            "startItem": (page - 1) * 50 + 1,
            "endItem": min(page * 50, total_items) if total_items else 0,
            "totalItems": total_items,
        },
    }


def _make_detail_response(
    listing_id: str = "744000131433769",
    employment_label: str = "Full-time",
    description_html: str = "<p>Project Manager role.</p>",
    posting_url: str = "https://jobs.smartrecruiters.com/TurnerTownsend/744000131433769",
    experience_label: str = "Mid-Senior",
) -> dict:
    """Build a synthetic SmartRecruiters job detail response."""
    return {
        "id": listing_id,
        "typeOfEmployment": {"id": "permanent", "label": employment_label},
        "postingUrl": posting_url,
        "experienceLevel": {"id": "mid_senior_level", "label": experience_label},
        "jobAd": {
            "sections": {
                "jobDescription": {
                    "title": "Job Description",
                    "text": description_html,
                }
            }
        },
    }


def _mock_client(post_responses: list[dict], get_responses: dict[str, dict] | None = None):
    """Build a mock httpx.AsyncClient for use with `patch('httpx.AsyncClient')`.

    post_responses: ordered list of JSON payloads for sequential POST calls.
    get_responses:  dict mapping URL → JSON payload for GET calls (detail enrichment).
    """
    get_responses = get_responses or {}
    post_call_index = [0]

    async def _post(url, json=None, **kwargs):
        idx = post_call_index[0]
        post_call_index[0] += 1
        resp = MagicMock()
        resp.status_code = 200
        payload = post_responses[idx] if idx < len(post_responses) else _make_search_response([])
        resp.json.return_value = payload
        return resp

    async def _get(url, **kwargs):
        resp = MagicMock()
        if url in get_responses:
            resp.status_code = 200
            resp.json.return_value = get_responses[url]
        else:
            resp.status_code = 404
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.post = AsyncMock(side_effect=_post)
    mock.get = AsyncMock(side_effect=_get)
    return mock


# ===========================================================================
# Helper function unit tests
# ===========================================================================


class TestExtractListingId:
    def test_valid_numeric_tail(self):
        ref = f"{_REF_BASE}744000131433769"
        assert _extract_listing_id(ref) == "744000131433769"

    def test_trailing_slash_stripped(self):
        ref = f"{_REF_BASE}744000131433769/"
        assert _extract_listing_id(ref) == "744000131433769"

    def test_non_numeric_tail_returns_none(self):
        ref = "https://api.smartrecruiters.com/v1/companies/TurnerTownsend/postings/abc-def"
        assert _extract_listing_id(ref) is None

    def test_empty_string_returns_none(self):
        assert _extract_listing_id("") is None

    def test_none_returns_none(self):
        assert _extract_listing_id(None) is None  # type: ignore[arg-type]


class TestParsePostedDate:
    def test_iso_z_suffix(self):
        dt = _parse_posted_date("2026-06-10T11:50:27.291Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 10
        assert dt.tzinfo == timezone.utc

    def test_iso_with_ms_and_plus_zero(self):
        dt = _parse_posted_date("2026-06-10T11:50:27.291+00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 10

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


class TestBuildLocationRaw:
    def test_city_and_country(self):
        item = {"location": {"city": "Sheffield", "country": "United Kingdom"}}
        assert _build_location_raw(item) == "Sheffield, United Kingdom"

    def test_city_only(self):
        item = {"location": {"city": "Leeds", "country": ""}}
        assert _build_location_raw(item) == "Leeds"

    def test_country_only(self):
        item = {"location": {"city": "", "country": "United Kingdom"}}
        assert _build_location_raw(item) == "United Kingdom"

    def test_missing_location_key(self):
        assert _build_location_raw({}) is None

    def test_both_empty_returns_none(self):
        item = {"location": {"city": "", "country": ""}}
        assert _build_location_raw(item) is None

    def test_whitespace_stripped(self):
        item = {"location": {"city": "  London  ", "country": "  United Kingdom  "}}
        assert _build_location_raw(item) == "London, United Kingdom"


class TestGetCustomField:
    def test_finds_matching_label(self):
        item = _make_item(discipline="Project Management")
        assert _get_custom_field(item, "Discipline") == "Project Management"

    def test_non_matching_label_returns_none(self):
        item = _make_item()
        assert _get_custom_field(item, "NonExistentField") is None

    def test_empty_custom_field_list(self):
        item = {"customField": []}
        assert _get_custom_field(item, "Discipline") is None

    def test_missing_custom_field_key(self):
        assert _get_custom_field({}, "Discipline") is None

    def test_finds_brands_field(self):
        item = _make_item()
        assert _get_custom_field(item, "Brands") == "Turner & Townsend"


class TestContentItemToRawListing:
    def test_returns_raw_listing(self):
        item = _make_item()
        listing = _content_item_to_raw_listing(item)
        assert listing is not None

    def test_source_is_turner_townsend(self):
        listing = _content_item_to_raw_listing(_make_item())
        assert listing.source == _SOURCE_NAME

    def test_employer_is_turner_townsend(self):
        listing = _content_item_to_raw_listing(_make_item())
        assert listing.employer == _EMPLOYER

    def test_source_listing_id_extracted(self):
        listing = _content_item_to_raw_listing(_make_item(listing_id="744000131433769"))
        assert listing.source_listing_id == "744000131433769"

    def test_title_populated(self):
        listing = _content_item_to_raw_listing(_make_item(name="Senior PM - Rail"))
        assert listing.title == "Senior PM - Rail"

    def test_url_is_ref_before_enrichment(self):
        listing = _content_item_to_raw_listing(_make_item(listing_id="744000999"))
        assert listing.url == f"{_REF_BASE}744000999"

    def test_location_raw_populated(self):
        listing = _content_item_to_raw_listing(_make_item(city="Sheffield", country="United Kingdom"))
        assert listing.location_raw == "Sheffield, United Kingdom"

    def test_posted_at_parsed(self):
        listing = _content_item_to_raw_listing(_make_item(released_date="2026-06-10T11:50:27.291Z"))
        assert listing.posted_at is not None
        assert listing.posted_at.year == 2026
        assert listing.posted_at.month == 6
        assert listing.posted_at.day == 10

    def test_contract_type_raw_is_none_pre_enrichment(self):
        listing = _content_item_to_raw_listing(_make_item())
        assert listing.contract_type_raw is None

    def test_description_raw_is_none_pre_enrichment(self):
        listing = _content_item_to_raw_listing(_make_item())
        assert listing.description_raw is None

    def test_metadata_department(self):
        listing = _content_item_to_raw_listing(_make_item(department_label="Real estate"))
        assert listing.metadata["department"] == "Real estate"

    def test_metadata_discipline(self):
        listing = _content_item_to_raw_listing(_make_item(discipline="Project Management"))
        assert listing.metadata["discipline"] == "Project Management"

    def test_metadata_detail_fetched_false(self):
        listing = _content_item_to_raw_listing(_make_item())
        assert listing.metadata["detail_fetched"] is False

    def test_missing_name_returns_none(self):
        item = _make_item()
        item["name"] = ""
        assert _content_item_to_raw_listing(item) is None

    def test_non_numeric_ref_returns_none(self):
        item = _make_item()
        item["ref"] = "https://api.smartrecruiters.com/v1/companies/TurnerTownsend/postings/abc"
        assert _content_item_to_raw_listing(item) is None

    def test_missing_ref_returns_none(self):
        item = _make_item()
        item["ref"] = ""
        assert _content_item_to_raw_listing(item) is None


# ===========================================================================
# _apply_detail tests
# ===========================================================================


class TestApplyDetail:
    def _base_listing(self):
        return _content_item_to_raw_listing(_make_item())

    def test_contract_type_raw_populated(self):
        listing = self._base_listing()
        detail = _make_detail_response(employment_label="Full-time")
        enriched = _apply_detail(listing, detail)
        assert enriched.contract_type_raw == "Full-time"

    def test_description_raw_populated(self):
        listing = self._base_listing()
        detail = _make_detail_response(description_html="<p>A great PM role.</p>")
        enriched = _apply_detail(listing, detail)
        assert enriched.description_raw == "<p>A great PM role.</p>"

    def test_url_replaced_with_posting_url(self):
        listing = self._base_listing()
        posting_url = "https://jobs.smartrecruiters.com/TurnerTownsend/744000131433769"
        detail = _make_detail_response(posting_url=posting_url)
        enriched = _apply_detail(listing, detail)
        assert enriched.url == posting_url

    def test_experience_level_in_metadata(self):
        listing = self._base_listing()
        detail = _make_detail_response(experience_label="Senior")
        enriched = _apply_detail(listing, detail)
        assert enriched.metadata["experience_level"] == "Senior"

    def test_detail_fetched_set_true(self):
        listing = self._base_listing()
        enriched = _apply_detail(listing, _make_detail_response())
        assert enriched.metadata["detail_fetched"] is True

    def test_missing_posting_url_keeps_original_url(self):
        listing = self._base_listing()
        original_url = listing.url
        detail = _make_detail_response()
        detail.pop("postingUrl")
        enriched = _apply_detail(listing, detail)
        assert enriched.url == original_url

    def test_missing_type_of_employment_keeps_none(self):
        listing = self._base_listing()
        detail = _make_detail_response()
        detail.pop("typeOfEmployment")
        enriched = _apply_detail(listing, detail)
        assert enriched.contract_type_raw is None

    def test_missing_experience_level_not_in_metadata(self):
        listing = self._base_listing()
        detail = _make_detail_response()
        detail.pop("experienceLevel")
        enriched = _apply_detail(listing, detail)
        assert "experience_level" not in enriched.metadata

    def test_original_listing_unchanged(self):
        """model_copy must not mutate the original."""
        listing = self._base_listing()
        _apply_detail(listing, _make_detail_response())
        assert listing.contract_type_raw is None
        assert listing.metadata["detail_fetched"] is False


# ===========================================================================
# Adapter constructor tests
# ===========================================================================


class TestAdapterConstructor:
    def test_default_name(self):
        assert TurnerTownsendAdapter().name == "turner_townsend"

    def test_default_crawl_delay(self):
        assert TurnerTownsendAdapter().crawl_delay == 3

    def test_default_max_pages(self):
        assert TurnerTownsendAdapter().max_pages_per_query == 5

    def test_enrich_detail_defaults_false(self):
        assert TurnerTownsendAdapter().enrich_detail is False

    def test_default_keywords_list_non_empty(self):
        assert len(TurnerTownsendAdapter().keywords_list) >= 1

    def test_custom_keywords_list(self):
        kws = ["project manager", "project director"]
        adapter = TurnerTownsendAdapter(keywords_list=kws)
        assert adapter.keywords_list == kws

    def test_custom_crawl_delay(self):
        assert TurnerTownsendAdapter(crawl_delay=10).crawl_delay == 10

    def test_enrich_detail_enabled(self):
        assert TurnerTownsendAdapter(enrich_detail=True).enrich_detail is True

    def test_unknown_kwargs_accepted(self):
        """Extra config.toml keys must not raise."""
        adapter = TurnerTownsendAdapter(unknown_key="value", another_key=123)
        assert adapter is not None


# ===========================================================================
# fetch() integration tests (mocked HTTP)
# ===========================================================================


class TestFetchSinglePage:
    """Single keyword, single page scenarios."""

    def test_single_page_returns_all_items(self):
        items = [_make_item(listing_id=str(700 + i), name=f"PM Role {i}") for i in range(3)]
        response = _make_search_response(items, page=1, total_pages=1)
        mock_client = _mock_client([response])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                adapter = TurnerTownsendAdapter(
                    keywords_list=["project manager"],
                    max_pages_per_query=5,
                    crawl_delay=0,
                )
                result = asyncio.run(adapter.fetch())

        assert len(result) == 3

    def test_source_field_correct(self):
        items = [_make_item(listing_id="123456")]
        response = _make_search_response(items)
        mock_client = _mock_client([response])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(TurnerTownsendAdapter(keywords_list=["pm"]).fetch())

        assert all(r.source == "turner_townsend" for r in result)

    def test_employer_field_correct(self):
        items = [_make_item(listing_id="111222")]
        response = _make_search_response(items)
        mock_client = _mock_client([response])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(TurnerTownsendAdapter(keywords_list=["pm"]).fetch())

        assert all(r.employer == "Turner & Townsend" for r in result)

    def test_empty_first_page_returns_empty_list(self):
        response = _make_search_response([])
        mock_client = _mock_client([response])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(keywords_list=["project manager"]).fetch()
                )

        assert result == []

    def test_http_error_returns_empty_list(self):
        async def _bad_post(url, json=None, **kwargs):
            resp = MagicMock()
            resp.status_code = 500
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_bad_post)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(keywords_list=["project manager"]).fetch()
                )

        assert result == []

    def test_since_filter_excludes_old_listings(self):
        old_item = _make_item(listing_id="111", released_date="2025-01-01T00:00:00.000Z")
        new_item = _make_item(listing_id="222", released_date="2026-06-10T00:00:00.000Z")
        response = _make_search_response([old_item, new_item])
        mock_client = _mock_client([response])

        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(keywords_list=["pm"]).fetch(since=since)
                )

        assert len(result) == 1
        assert result[0].source_listing_id == "222"


class TestFetchPagination:
    """Multi-page pagination tests."""

    def test_fetches_all_pages_up_to_total(self):
        page1_items = [_make_item(listing_id=str(i)) for i in range(1, 4)]
        page2_items = [_make_item(listing_id=str(i)) for i in range(4, 7)]
        resp1 = _make_search_response(page1_items, page=1, total_pages=2)
        resp2 = _make_search_response(page2_items, page=2, total_pages=2)
        mock_client = _mock_client([resp1, resp2])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["project manager"],
                        max_pages_per_query=10,
                    ).fetch()
                )

        assert len(result) == 6

    def test_stops_at_total_pages(self):
        items = [_make_item(listing_id=str(i)) for i in range(3)]
        resp1 = _make_search_response(items, page=1, total_pages=1)
        mock_client = _mock_client([resp1])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["project manager"],
                        max_pages_per_query=10,
                    ).fetch()
                )

        # Only 1 POST should have been made (page 1 = last page)
        assert mock_client.post.call_count == 1
        assert len(result) == 3

    def test_stops_at_max_pages_per_query(self):
        """Adapter must respect max_pages_per_query cap even if totalPages is higher."""
        items = [_make_item(listing_id=str(i)) for i in range(3)]
        # 5 total pages reported, but max_pages_per_query=2
        resp1 = _make_search_response(items, page=1, total_pages=5)
        resp2 = _make_search_response(
            [_make_item(listing_id=str(10 + i)) for i in range(3)], page=2, total_pages=5
        )
        mock_client = _mock_client([resp1, resp2])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["project manager"],
                        max_pages_per_query=2,
                    ).fetch()
                )

        assert mock_client.post.call_count == 2
        assert len(result) == 6

    def test_stops_on_empty_page(self):
        """An unexpectedly empty page should stop pagination early."""
        resp1 = _make_search_response(
            [_make_item(listing_id="1")], page=1, total_pages=5
        )
        resp2 = _make_search_response([], page=2, total_pages=5)
        mock_client = _mock_client([resp1, resp2])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["pm"],
                        max_pages_per_query=5,
                    ).fetch()
                )

        assert len(result) == 1
        assert mock_client.post.call_count == 2


class TestFetchDedup:
    """Deduplication across keywords and pages."""

    def test_dedup_across_keywords(self):
        """Same listing ID from two keywords must appear only once."""
        shared_item = _make_item(listing_id="999", name="Shared PM Role")
        unique_item = _make_item(listing_id="888", name="Unique PM Role")

        resp_kw1 = _make_search_response([shared_item, unique_item])
        resp_kw2 = _make_search_response([shared_item])  # duplicate
        mock_client = _mock_client([resp_kw1, resp_kw2])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["project manager", "project director"],
                        max_pages_per_query=1,
                    ).fetch()
                )

        ids = [r.source_listing_id for r in result]
        assert len(result) == 2
        assert ids.count("999") == 1

    def test_dedup_within_pages(self):
        """Same listing ID appearing on multiple pages must be deduplicated."""
        item = _make_item(listing_id="777")
        resp1 = _make_search_response([item], page=1, total_pages=2)
        resp2 = _make_search_response([item], page=2, total_pages=2)
        mock_client = _mock_client([resp1, resp2])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["pm"],
                        max_pages_per_query=5,
                    ).fetch()
                )

        assert len(result) == 1


class TestFetchEnrichment:
    """Optional detail enrichment via SmartRecruiters GET."""

    def test_enrichment_disabled_by_default(self):
        items = [_make_item(listing_id="111")]
        resp = _make_search_response(items)
        mock_client = _mock_client([resp])

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(keywords_list=["pm"]).fetch()
                )

        # No GET calls should have been made
        assert mock_client.get.call_count == 0
        assert result[0].metadata["detail_fetched"] is False

    def test_enrichment_populates_contract_type(self):
        listing_id = "744000131433769"
        ref_url = f"{_REF_BASE}{listing_id}"
        items = [_make_item(listing_id=listing_id)]
        resp = _make_search_response(items)
        detail = _make_detail_response(listing_id=listing_id, employment_label="Full-time")
        mock_client = _mock_client([resp], get_responses={ref_url: detail})

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["pm"],
                        enrich_detail=True,
                        detail_delay_seconds=0,
                    ).fetch()
                )

        assert len(result) == 1
        assert result[0].contract_type_raw == "Full-time"
        assert result[0].metadata["detail_fetched"] is True

    def test_enrichment_populates_description(self):
        listing_id = "744000131433769"
        ref_url = f"{_REF_BASE}{listing_id}"
        items = [_make_item(listing_id=listing_id)]
        resp = _make_search_response(items)
        detail = _make_detail_response(
            listing_id=listing_id, description_html="<p>Senior PM role in Sheffield.</p>"
        )
        mock_client = _mock_client([resp], get_responses={ref_url: detail})

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["pm"],
                        enrich_detail=True,
                        detail_delay_seconds=0,
                    ).fetch()
                )

        assert result[0].description_raw == "<p>Senior PM role in Sheffield.</p>"

    def test_enrichment_replaces_url_with_posting_url(self):
        listing_id = "744000131433769"
        ref_url = f"{_REF_BASE}{listing_id}"
        posting_url = f"https://jobs.smartrecruiters.com/TurnerTownsend/{listing_id}"
        items = [_make_item(listing_id=listing_id)]
        resp = _make_search_response(items)
        detail = _make_detail_response(listing_id=listing_id, posting_url=posting_url)
        mock_client = _mock_client([resp], get_responses={ref_url: detail})

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["pm"],
                        enrich_detail=True,
                        detail_delay_seconds=0,
                    ).fetch()
                )

        assert result[0].url == posting_url

    def test_enrichment_404_leaves_listing_unchanged(self):
        """404 on detail fetch must not raise and must preserve original listing."""
        listing_id = "744000131433769"
        items = [_make_item(listing_id=listing_id)]
        resp = _make_search_response(items)
        # get_responses is empty → mock_client returns 404 for GET
        mock_client = _mock_client([resp], get_responses={})

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["pm"],
                        enrich_detail=True,
                        detail_delay_seconds=0,
                    ).fetch()
                )

        assert len(result) == 1
        assert result[0].contract_type_raw is None
        assert result[0].metadata["detail_fetched"] is False

    def test_enrichment_timeout_leaves_listing_unchanged(self):
        """TimeoutException on detail fetch must not propagate."""
        import httpx as _httpx

        listing_id = "744000131433769"
        ref_url = f"{_REF_BASE}{listing_id}"
        items = [_make_item(listing_id=listing_id)]
        resp = _make_search_response(items)

        async def _timeout_get(url, **kwargs):
            raise _httpx.TimeoutException("timed out", request=None)

        mock_client = _mock_client([resp])
        mock_client.get = AsyncMock(side_effect=_timeout_get)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("asyncio.sleep", AsyncMock(return_value=None)):
                result = asyncio.run(
                    TurnerTownsendAdapter(
                        keywords_list=["pm"],
                        enrich_detail=True,
                        detail_delay_seconds=0,
                    ).fetch()
                )

        assert len(result) == 1
        assert result[0].contract_type_raw is None
