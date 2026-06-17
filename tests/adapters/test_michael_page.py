"""Fixture-driven unit tests for the Michael Page adapter.

Loads the saved HTML snapshot from:
    tests/fixtures/adapters/michael_page_page1.html

and asserts that _parse_html() populates the expected fields correctly.

Confirmed DOM structure (live recon 2026-06-17):
  li.views-row div.job-tile          → job card
  div.job-title id attr              → source_listing_id (numeric node ID)
  div.job-title h3 a                 → title + url (site-relative href)
  div.job-location                   → location_raw (text after icon)
  div.job-contract-type              → contract_type_raw ("Interim", "Temporary", "Permanent")
  div.job-salary                     → salary_raw (optional; "£320 - £375 per day" or annual)
  div.job_advert__job-summary-text   → description_raw (paragraph)
  div.job_advert__job-desc-bullet-points → description_raw (bullet points, appended)

Fixture page (project-manager, no contract filter):
  30 cards — 18 Permanent, 7 Interim, 5 Temporary.
  3 day-rate listings (£320-375/day, £500-550/day, £400-450/day).
"""
from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # noqa: F401 (used for fixtures and marks)

from mechpm.adapters.michael_page import (
    MichaelPageAdapter,
    _build_search_urls,
    _keyword_to_slug,
    _parse_html,
)

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "adapters"
    / "michael_page_page1.html"
)
_PAGE_URL = "https://www.michaelpage.co.uk/jobs/project-manager"
_BASE_URL = "https://www.michaelpage.co.uk"


@pytest.fixture(scope="module")
def parsed_listings():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    return _parse_html(html, _PAGE_URL)


# ---------------------------------------------------------------------------
# T1: Volume
# ---------------------------------------------------------------------------

def test_listing_count(parsed_listings):
    """Fixture must yield 30 listings (one per job-tile card on page 1)."""
    assert len(parsed_listings) == 30


# ---------------------------------------------------------------------------
# T2: Source identifier
# ---------------------------------------------------------------------------

def test_source_is_michael_page(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == "michael_page"


# ---------------------------------------------------------------------------
# T3: Agency is always Michael Page
# ---------------------------------------------------------------------------

def test_agency_michael_page(parsed_listings):
    for listing in parsed_listings:
        assert listing.agency == "Michael Page", (
            f"agency should be 'Michael Page', got {listing.agency!r}"
        )


# ---------------------------------------------------------------------------
# T4: Listing ID is a non-empty numeric string
# ---------------------------------------------------------------------------

def test_source_listing_id_numeric(parsed_listings):
    for listing in parsed_listings:
        assert listing.source_listing_id, "source_listing_id must be non-empty"
        assert listing.source_listing_id.isdigit(), (
            f"source_listing_id {listing.source_listing_id!r} is not numeric"
        )


def test_first_listing_id(parsed_listings):
    """First card's numeric node ID must be 9282906."""
    assert parsed_listings[0].source_listing_id == "9282906"


# ---------------------------------------------------------------------------
# T5: Title
# ---------------------------------------------------------------------------

def test_title_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.title, "title must be non-empty"


def test_first_listing_title(parsed_listings):
    assert parsed_listings[0].title == "Project Manager"


# ---------------------------------------------------------------------------
# T6: URL is absolute and points to job-detail
# ---------------------------------------------------------------------------

def test_url_is_absolute(parsed_listings):
    for listing in parsed_listings:
        assert listing.url.startswith("https://"), (
            f"url should be absolute: {listing.url!r}"
        )


def test_first_listing_url(parsed_listings):
    expected = (
        "https://www.michaelpage.co.uk/job-detail/project-manager/ref/jn-032026-6971462"
    )
    assert parsed_listings[0].url == expected


def test_url_contains_job_detail(parsed_listings):
    for listing in parsed_listings:
        assert "/job-detail/" in listing.url, (
            f"url should contain /job-detail/: {listing.url!r}"
        )


# ---------------------------------------------------------------------------
# T7: Location (optional — may be absent on some cards)
# ---------------------------------------------------------------------------

def test_first_listing_location(parsed_listings):
    assert parsed_listings[0].location_raw == "City of London"


def test_location_is_string_or_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.location_raw is None or isinstance(listing.location_raw, str)


# ---------------------------------------------------------------------------
# T8: Contract type values
# ---------------------------------------------------------------------------

def test_contract_type_values(parsed_listings):
    """contract_type_raw must be one of the three known MP values."""
    allowed = {"Interim", "Temporary", "Permanent"}
    for listing in parsed_listings:
        assert listing.contract_type_raw in allowed, (
            f"Unexpected contract_type_raw: {listing.contract_type_raw!r}"
        )


def test_first_listing_contract_type(parsed_listings):
    assert parsed_listings[0].contract_type_raw == "Interim"


def test_contract_type_distribution(parsed_listings):
    """Fixture contains Permanent, Interim, and Temporary listings."""
    from collections import Counter
    dist = Counter(l.contract_type_raw for l in parsed_listings)
    assert dist["Permanent"] > 0, "Expected some Permanent listings in fixture"
    assert dist["Interim"] > 0, "Expected some Interim listings in fixture"
    assert dist["Temporary"] > 0, "Expected some Temporary listings in fixture"


def test_permanent_listings_present(parsed_listings):
    """Fixture intentionally includes Permanent listings to exercise filtering."""
    permanent = [l for l in parsed_listings if l.contract_type_raw == "Permanent"]
    assert len(permanent) >= 10, (
        f"Expected ≥10 Permanent listings in fixture, got {len(permanent)}"
    )


def test_contract_listings_present(parsed_listings):
    """Fixture must include at least 5 Interim/Temporary listings."""
    contract = [
        l for l in parsed_listings
        if l.contract_type_raw in ("Interim", "Temporary")
    ]
    assert len(contract) >= 5, (
        f"Expected ≥5 contract listings in fixture, got {len(contract)}"
    )


# ---------------------------------------------------------------------------
# T9: Salary (optional — day-rate and annual formats present)
# ---------------------------------------------------------------------------

def test_salary_is_string_or_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.salary_raw is None or isinstance(listing.salary_raw, str)


def test_day_rate_listings_present(parsed_listings):
    """At least one listing should have a day-rate salary (£X per day)."""
    day_rate = [l for l in parsed_listings if l.salary_raw and "per day" in l.salary_raw]
    assert len(day_rate) >= 1, "Expected at least one day-rate salary listing"


def test_day_rate_salary_format(parsed_listings):
    """Day-rate salaries must contain £ and 'per day'."""
    day_rate = [l for l in parsed_listings if l.salary_raw and "per day" in l.salary_raw]
    for listing in day_rate:
        assert "\xa3" in listing.salary_raw, (
            f"Day-rate salary missing £ sign: {listing.salary_raw!r}"
        )


def test_first_day_rate_listing(parsed_listings):
    """Card at index 11 (id=9470121) has salary '£320 - £375 per day'."""
    target = next(
        (l for l in parsed_listings if l.source_listing_id == "9470121"), None
    )
    assert target is not None, "Listing 9470121 not found in fixture"
    assert target.salary_raw is not None
    assert "per day" in target.salary_raw
    assert "320" in target.salary_raw


# ---------------------------------------------------------------------------
# T10: Description text
# ---------------------------------------------------------------------------

def test_description_non_empty(parsed_listings):
    """All cards must have a non-empty description_raw (summary is always present)."""
    for listing in parsed_listings:
        assert listing.description_raw, (
            f"description_raw should not be empty for listing {listing.source_listing_id}"
        )


def test_description_contains_job_text(parsed_listings):
    """Description should contain meaningful text (not just whitespace)."""
    for listing in parsed_listings:
        assert len(listing.description_raw) > 20, (
            f"description_raw too short for listing {listing.source_listing_id}"
        )


# ---------------------------------------------------------------------------
# T11: posted_at is always None (not available in MP search cards)
# ---------------------------------------------------------------------------

def test_posted_at_none(parsed_listings):
    """Michael Page search cards do not include a posted date."""
    for listing in parsed_listings:
        assert listing.posted_at is None, (
            f"posted_at should be None for listing {listing.source_listing_id}"
        )


# ---------------------------------------------------------------------------
# T12: metadata fields
# ---------------------------------------------------------------------------

def test_metadata_detail_fetched(parsed_listings):
    for listing in parsed_listings:
        assert listing.metadata.get("detail_fetched") is False


def test_metadata_page_url(parsed_listings):
    for listing in parsed_listings:
        assert listing.metadata.get("page_url") == _PAGE_URL


# ---------------------------------------------------------------------------
# T13: Keyword-to-slug conversion
# ---------------------------------------------------------------------------

class TestKeywordToSlug:
    def test_spaces_become_hyphens(self):
        assert _keyword_to_slug("project manager") == "project-manager"

    def test_multi_word(self):
        assert _keyword_to_slug("contracts manager") == "contracts-manager"

    def test_already_hyphenated(self):
        assert _keyword_to_slug("project-manager") == "project-manager"

    def test_uppercase_lowercased(self):
        assert _keyword_to_slug("Project Manager") == "project-manager"

    def test_extra_spaces_collapsed(self):
        assert _keyword_to_slug("  project  manager  ") == "project-manager"


# ---------------------------------------------------------------------------
# T14: Search URL builder
# ---------------------------------------------------------------------------

class TestBuildSearchUrls:
    def test_single_keyword_single_page(self):
        urls = _build_search_urls(["project-manager"], max_pages=1)
        assert len(urls) == 1
        assert urls[0][0] == "https://www.michaelpage.co.uk/jobs/project-manager"
        assert urls[0][1] == "project-manager"

    def test_page_0_has_no_page_param(self):
        urls = _build_search_urls(["project-manager"], max_pages=3)
        assert "?page=" not in urls[0][0]

    def test_page_1_has_page_param(self):
        urls = _build_search_urls(["project-manager"], max_pages=3)
        assert "?page=1" in urls[1][0]

    def test_page_2_url(self):
        urls = _build_search_urls(["project-manager"], max_pages=3)
        assert urls[2][0].endswith("?page=2")

    def test_multi_keyword_count(self):
        urls = _build_search_urls(["project-manager", "project-engineer"], max_pages=2)
        assert len(urls) == 4  # 2 keywords × 2 pages

    def test_keyword_is_passed_through(self):
        urls = _build_search_urls(["contracts-manager"], max_pages=1)
        assert urls[0][1] == "contracts-manager"

    def test_keyword_with_spaces_slugified(self):
        urls = _build_search_urls(["contracts manager"], max_pages=1)
        assert "contracts-manager" in urls[0][0]


# ---------------------------------------------------------------------------
# T15: Adapter constructor defaults
# ---------------------------------------------------------------------------

class TestAdapterConstructor:
    def test_default_name(self):
        adapter = MichaelPageAdapter()
        assert adapter.name == "michael_page"

    def test_default_crawl_delay(self):
        adapter = MichaelPageAdapter()
        assert adapter.crawl_delay == 5

    def test_custom_crawl_delay(self):
        adapter = MichaelPageAdapter(crawl_delay=10)
        assert adapter.crawl_delay == 10

    def test_default_keywords(self):
        adapter = MichaelPageAdapter()
        assert adapter.keywords_list == ["project-manager"]

    def test_custom_keywords(self):
        kws = ["project-manager", "contracts-manager"]
        adapter = MichaelPageAdapter(keywords_list=kws)
        assert adapter.keywords_list == kws

    def test_default_max_pages(self):
        adapter = MichaelPageAdapter()
        assert adapter.max_pages_per_query == 3


# ---------------------------------------------------------------------------
# T16: Empty HTML handling
# ---------------------------------------------------------------------------

def test_empty_html_returns_empty_list():
    result = _parse_html("", "https://www.michaelpage.co.uk/jobs/project-manager")
    assert result == []


def test_html_with_no_cards_returns_empty_list():
    result = _parse_html("<html><body><div>No jobs</div></body></html>", "http://test")
    assert result == []


# ---------------------------------------------------------------------------
# T17: Adapter fetch with mocked HTTP (404 handling)
# ---------------------------------------------------------------------------

def test_fetch_404_keyword_returns_empty():
    """Adapter must return [] and not raise when keyword URL returns 404."""
    import asyncio
    adapter = MichaelPageAdapter(
        crawl_delay=0,
        keywords_list=["nonexistent-keyword"],
        max_pages_per_query=1,
    )
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = ""

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(adapter.fetch())
        assert result == []


def test_fetch_returns_listings_from_fixture():
    """Adapter must parse real HTML and return a non-empty list."""
    import asyncio
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = MichaelPageAdapter(
        crawl_delay=0,
        keywords_list=["project-manager"],
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
        assert len(result) == 30
        assert all(r.source == "michael_page" for r in result)


def test_fetch_deduplicates_across_keywords():
    """When two keywords return overlapping listings, dedup by source_listing_id."""
    import asyncio
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = MichaelPageAdapter(
        crawl_delay=0,
        keywords_list=["project-manager", "project-manager"],  # deliberate duplicate
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
        ids = [r.source_listing_id for r in result]
        assert len(ids) == len(set(ids)), "Duplicate source_listing_ids found"
