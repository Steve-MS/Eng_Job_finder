"""Fixture-driven unit tests for the Manpower Group adapter.

Loads the saved HTML snapshot from:
    tests/fixtures/adapters/manpower_group_page1.html

and asserts that _parse_html() populates the expected fields correctly.

Confirmed DOM structure (live recon 2026-06-17):
  li.job-result-item              → job card
  div.job-title a                 → title (text) + url (href; site-relative)
  href slug numeric suffix        → source_listing_id (e.g., "-5927521" → "5927521")
  li.results-job-location         → location_raw
  li.results-salary               → salary_raw (optional)
  li.results-posted-at            → posted_at (parsed from relative text)
  p.job-description               → description_raw (short snippet)
  figure.recruiter-figure img[alt]→ agency

Fixture page (query=project+manager, page 1):
  6 job cards.
  No contract_type_raw in search cards (always None).
  Relative posted dates: "Posted 12 days ago", "Posted 1 day ago", etc.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.manpower_group import (
    ManpowerGroupAdapter,
    _build_search_url,
    _has_next_page,
    _parse_html,
    _parse_posted_at,
)

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "adapters"
    / "manpower_group_page1.html"
)
_PAGE_URL = (
    "https://careers.manpowergroup.co.uk/jobs"
    "?sort_type=relevance&query=project+manager&radius=1600km"
)
_BASE_URL = "https://careers.manpowergroup.co.uk"


@pytest.fixture(scope="module")
def parsed_listings():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    return _parse_html(html, _PAGE_URL)


@pytest.fixture(scope="module")
def fixture_html():
    return _FIXTURE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T1: Volume
# ---------------------------------------------------------------------------

def test_listing_count(parsed_listings):
    """Fixture page yields 6 listings (one per li.job-result-item card)."""
    assert len(parsed_listings) == 6


# ---------------------------------------------------------------------------
# T2: Source identifier
# ---------------------------------------------------------------------------

def test_source_is_manpower_group(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == "manpower_group"


# ---------------------------------------------------------------------------
# T3: Listing ID is a non-empty numeric string
# ---------------------------------------------------------------------------

def test_source_listing_id_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.source_listing_id, "source_listing_id must be non-empty"


def test_source_listing_id_numeric(parsed_listings):
    for listing in parsed_listings:
        assert listing.source_listing_id.isdigit(), (
            f"source_listing_id {listing.source_listing_id!r} is not numeric"
        )


def test_first_listing_id(parsed_listings):
    """First card's numeric job ID must be 5927521."""
    assert parsed_listings[0].source_listing_id == "5927521"


# ---------------------------------------------------------------------------
# T4: Title
# ---------------------------------------------------------------------------

def test_title_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.title, "title must be non-empty"


def test_first_listing_title(parsed_listings):
    assert parsed_listings[0].title == "Business Development Manager - Public Sector"


# ---------------------------------------------------------------------------
# T5: URL is absolute and points to job detail
# ---------------------------------------------------------------------------

def test_url_is_absolute(parsed_listings):
    for listing in parsed_listings:
        assert listing.url.startswith("https://"), (
            f"url should be absolute: {listing.url!r}"
        )


def test_url_contains_base_domain(parsed_listings):
    for listing in parsed_listings:
        assert "careers.manpowergroup.co.uk" in listing.url, (
            f"url should contain base domain: {listing.url!r}"
        )


def test_url_contains_job_path(parsed_listings):
    for listing in parsed_listings:
        assert "/job/" in listing.url, (
            f"url should contain /job/: {listing.url!r}"
        )


def test_first_listing_url(parsed_listings):
    expected = (
        "https://careers.manpowergroup.co.uk"
        "/job/business-development-manager-public-sector-5927521"
    )
    assert parsed_listings[0].url == expected


# ---------------------------------------------------------------------------
# T6: Location (optional — may be absent on some cards)
# ---------------------------------------------------------------------------

def test_location_is_string_or_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.location_raw is None or isinstance(listing.location_raw, str)


def test_first_listing_location(parsed_listings):
    assert parsed_listings[0].location_raw == "England"


def test_some_listings_have_location(parsed_listings):
    located = [l for l in parsed_listings if l.location_raw]
    assert len(located) >= 1, "Expected at least one listing with a location"


# ---------------------------------------------------------------------------
# T7: Contract type is always None (not in search cards)
# ---------------------------------------------------------------------------

def test_contract_type_is_none(parsed_listings):
    """Manpower Group search cards do not expose contract type."""
    for listing in parsed_listings:
        assert listing.contract_type_raw is None, (
            f"contract_type_raw should be None, got {listing.contract_type_raw!r}"
        )


# ---------------------------------------------------------------------------
# T8: Salary (optional — textual descriptions)
# ---------------------------------------------------------------------------

def test_salary_is_string_or_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.salary_raw is None or isinstance(listing.salary_raw, str)


def test_some_listings_have_salary(parsed_listings):
    salaried = [l for l in parsed_listings if l.salary_raw]
    assert len(salaried) >= 1, "Expected at least one listing with salary info"


def test_first_listing_salary(parsed_listings):
    """First card should have a salary description."""
    assert parsed_listings[0].salary_raw is not None


# ---------------------------------------------------------------------------
# T9: Posted date (parsed from relative text)
# ---------------------------------------------------------------------------

def test_posted_at_is_datetime_or_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.posted_at is None or isinstance(listing.posted_at, datetime)


def test_posted_at_timezone_aware(parsed_listings):
    dated = [l for l in parsed_listings if l.posted_at is not None]
    for listing in dated:
        assert listing.posted_at.tzinfo is not None, (
            f"posted_at should be timezone-aware for listing {listing.source_listing_id}"
        )


def test_some_listings_have_posted_at(parsed_listings):
    dated = [l for l in parsed_listings if l.posted_at is not None]
    assert len(dated) >= 1, "Expected at least one listing with a parsed posted_at"


def test_posted_at_in_past(parsed_listings):
    """All parsed dates must be in the past."""
    now = datetime.now(timezone.utc)
    for listing in parsed_listings:
        if listing.posted_at is not None:
            assert listing.posted_at < now, (
                f"posted_at {listing.posted_at} is not in the past"
            )


# ---------------------------------------------------------------------------
# T10: Description snippet
# ---------------------------------------------------------------------------

def test_description_is_string_or_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.description_raw is None or isinstance(listing.description_raw, str)


def test_some_descriptions_present(parsed_listings):
    described = [l for l in parsed_listings if l.description_raw]
    assert len(described) >= 1, "Expected at least one listing with description"


def test_description_has_meaningful_length(parsed_listings):
    for listing in parsed_listings:
        if listing.description_raw:
            assert len(listing.description_raw) > 20, (
                f"description_raw too short for {listing.source_listing_id}"
            )


# ---------------------------------------------------------------------------
# T11: Agency from recruiter figure img alt
# ---------------------------------------------------------------------------

def test_agency_is_string_or_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.agency is None or isinstance(listing.agency, str)


def test_some_listings_have_agency(parsed_listings):
    agencied = [l for l in parsed_listings if l.agency]
    assert len(agencied) >= 1, "Expected at least one listing with agency name"


def test_first_listing_agency(parsed_listings):
    """First card's recruiter brand should be 'Experis'."""
    assert parsed_listings[0].agency == "Experis"


# ---------------------------------------------------------------------------
# T12: metadata fields
# ---------------------------------------------------------------------------

def test_metadata_detail_fetched(parsed_listings):
    for listing in parsed_listings:
        assert listing.metadata.get("detail_fetched") is False


def test_metadata_posted_raw(parsed_listings):
    for listing in parsed_listings:
        assert "posted_raw" in listing.metadata


# ---------------------------------------------------------------------------
# T13: IDs are unique within the page
# ---------------------------------------------------------------------------

def test_listing_ids_are_unique(parsed_listings):
    ids = [l.source_listing_id for l in parsed_listings]
    assert len(ids) == len(set(ids)), "Duplicate listing IDs within a page"


# ---------------------------------------------------------------------------
# T14: _parse_posted_at helper
# ---------------------------------------------------------------------------

class TestParsePostedAt:
    def test_one_day_ago(self):
        result = _parse_posted_at("Posted 1 day ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        delta = now - result
        assert 0 < delta.total_seconds() < 60 * 60 * 24 * 2  # between 0 and 48h

    def test_twelve_days_ago(self):
        result = _parse_posted_at("Posted 12 days ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        delta = now - result
        assert 11 * 86400 < delta.total_seconds() < 13 * 86400

    def test_about_one_month_ago(self):
        result = _parse_posted_at("Posted about 1 month ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        delta = now - result
        assert 25 * 86400 < delta.total_seconds() < 35 * 86400

    def test_two_weeks_ago(self):
        result = _parse_posted_at("Posted 2 weeks ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        delta = now - result
        assert 13 * 86400 < delta.total_seconds() < 15 * 86400

    def test_none_input(self):
        assert _parse_posted_at(None) is None

    def test_empty_string(self):
        assert _parse_posted_at("") is None

    def test_unrecognised_text(self):
        assert _parse_posted_at("Just now") is None

    def test_result_is_utc(self):
        result = _parse_posted_at("Posted 5 days ago")
        assert result is not None
        assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# T15: _build_search_url helper
# ---------------------------------------------------------------------------

class TestBuildSearchUrl:
    def test_page1_no_page_param(self):
        url = _build_search_url("project manager", 1)
        assert "&page=" not in url

    def test_page1_contains_query(self):
        url = _build_search_url("project manager", 1)
        assert "query=project+manager" in url

    def test_page2_has_page_param(self):
        url = _build_search_url("project manager", 2)
        assert "&page=2" in url

    def test_page3_url(self):
        url = _build_search_url("project director", 3)
        assert "&page=3" in url

    def test_radius_in_url(self):
        url = _build_search_url("project manager", 1)
        assert "radius=1600km" in url

    def test_sort_type_in_url(self):
        url = _build_search_url("project manager", 1)
        assert "sort_type=relevance" in url

    def test_spaces_encoded_as_plus(self):
        url = _build_search_url("project engineer mechanical", 1)
        assert "project+engineer+mechanical" in url


# ---------------------------------------------------------------------------
# T16: _has_next_page helper
# ---------------------------------------------------------------------------

def test_has_next_page_true(fixture_html):
    """Fixture page 1 has a next page link."""
    from selectolax.parser import HTMLParser
    tree = HTMLParser(fixture_html)
    assert _has_next_page(tree) is True


def test_has_next_page_false_on_empty():
    from selectolax.parser import HTMLParser
    tree = HTMLParser("<html><body>No jobs here</body></html>")
    assert _has_next_page(tree) is False


# ---------------------------------------------------------------------------
# T17: Adapter constructor defaults
# ---------------------------------------------------------------------------

class TestAdapterConstructor:
    def test_name(self):
        adapter = ManpowerGroupAdapter()
        assert adapter.name == "manpower_group"

    def test_default_crawl_delay(self):
        adapter = ManpowerGroupAdapter()
        assert adapter.crawl_delay == 5

    def test_custom_crawl_delay(self):
        adapter = ManpowerGroupAdapter(crawl_delay=10)
        assert adapter.crawl_delay == 10

    def test_default_keywords(self):
        adapter = ManpowerGroupAdapter()
        assert adapter.keywords_list == ["project manager"]

    def test_custom_keywords(self):
        kws = ["project director", "document controller"]
        adapter = ManpowerGroupAdapter(keywords_list=kws)
        assert adapter.keywords_list == kws

    def test_default_max_pages(self):
        adapter = ManpowerGroupAdapter()
        assert adapter.max_pages_per_query == 5


# ---------------------------------------------------------------------------
# T18: Empty HTML handling
# ---------------------------------------------------------------------------

def test_empty_html_returns_empty_list():
    result = _parse_html("", _PAGE_URL)
    assert result == []


def test_html_with_no_cards_returns_empty_list():
    result = _parse_html("<html><body><div>No jobs</div></body></html>", _PAGE_URL)
    assert result == []


# ---------------------------------------------------------------------------
# T19: Adapter fetch with mocked HTTP (non-200 handling)
# ---------------------------------------------------------------------------

def test_fetch_500_returns_empty():
    """Adapter must return [] and not raise when server returns 500."""
    import asyncio
    adapter = ManpowerGroupAdapter(
        crawl_delay=0,
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = ""

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(adapter.fetch())

    assert result == []


def test_fetch_uses_html_fixture_content():
    """Adapter must parse fixture HTML and return 6 listings."""
    import asyncio
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = ManpowerGroupAdapter(
        crawl_delay=0,
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = html

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(adapter.fetch())

    assert len(result) == 6


def test_fetch_deduplicates_across_keywords():
    """If the same listing appears under two keywords, only one copy is stored."""
    import asyncio
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = ManpowerGroupAdapter(
        crawl_delay=0,
        keywords_list=["project manager", "project director"],
        max_pages_per_query=1,
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = html

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(adapter.fetch())

    ids = [l.source_listing_id for l in result]
    assert len(ids) == len(set(ids)), "Cross-keyword deduplication failed"
    assert len(result) == 6  # same 6 listings, deduplicated
