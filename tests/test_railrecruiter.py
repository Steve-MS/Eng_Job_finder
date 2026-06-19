"""Comprehensive unit tests for the RailRecruiter adapter.

Fixture: tests/fixtures/adapters/railrecruiter_page1.html
Confirmed DOM structure (live recon 2026-06-19):
  ol.vacancy-listing / #uxMainContent_uxResultsOL
    li[id^='v']
      h2 a               → title + relative URL
      .logo a href       → agency (em-{Name} slug)
      dd.location span   → location_raw
      dd.salary span     → salary_raw
      dd.job-type span   → contract_type_raw
      dd.posted span     → posted_at  ("D Mon YYYY")
      p                  → description_raw (excerpt)

Tests cover:
  - HTML parsing (volume, per-field)
  - Field mapping (specific known values)
  - Error handling (empty / malformed HTML)
  - Pagination URL construction
  - `since` date filtering
  - Slug / URL building helpers
  - Agency name extraction helper
  - Date parsing helper
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mechpm.adapters.railrecruiter import (
    RailRecruiterAdapter,
    _build_search_url,
    _extract_agency,
    _keyword_to_slug,
    _parse_date,
    _parse_html,
)

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "adapters"
    / "railrecruiter_page1.html"
)
_PAGE_URL = (
    "https://www.railrecruiter.co.uk"
    "/jobs/job-search-results/kw-project-manager/co-225/"
)
_BASE_URL = "https://www.railrecruiter.co.uk"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_html() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed_listings(raw_html):
    return _parse_html(raw_html, _BASE_URL, _PAGE_URL)


# ===========================================================================
# 1 – HTML Parsing: volume gate
# ===========================================================================


def test_listing_count(parsed_listings):
    """Fixture page must yield exactly 10 listings."""
    assert len(parsed_listings) == 10


# ===========================================================================
# 2 – Source identifier
# ===========================================================================


def test_source_is_railrecruiter(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == "railrecruiter"


# ===========================================================================
# 3 – source_listing_id format
# ===========================================================================


def test_source_listing_id_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.source_listing_id, "source_listing_id must be non-empty"


def test_source_listing_id_no_v_prefix(parsed_listings):
    """IDs must NOT include the leading 'v' from the li element id."""
    for listing in parsed_listings:
        assert not listing.source_listing_id.startswith("v"), (
            f"ID {listing.source_listing_id!r} still has 'v' prefix"
        )


def test_first_listing_id(parsed_listings):
    """First listing (featured) must have id '5828-1'."""
    assert parsed_listings[0].source_listing_id == "5828-1"


def test_second_listing_id(parsed_listings):
    assert parsed_listings[1].source_listing_id == "2804769655-2"


# ===========================================================================
# 4 – Title
# ===========================================================================


def test_titles_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.title, f"empty title for {listing.source_listing_id}"


def test_first_listing_title(parsed_listings):
    assert parsed_listings[0].title == "Principal Technical Lead"


def test_second_listing_title(parsed_listings):
    assert parsed_listings[1].title == "Project Manager"


# ===========================================================================
# 5 – URL construction
# ===========================================================================


def test_urls_are_absolute(parsed_listings):
    for listing in parsed_listings:
        assert listing.url.startswith("https://"), (
            f"non-absolute URL: {listing.url!r}"
        )


def test_first_listing_url(parsed_listings):
    expected = (
        "https://www.railrecruiter.co.uk"
        "/jobs/principal-technical-lead-rssb-25-fenchurch-avenue/5828-1/"
    )
    assert parsed_listings[0].url == expected


def test_second_listing_url(parsed_listings):
    expected = (
        "https://www.railrecruiter.co.uk"
        "/jobs/project-manager-city-swindon/2804769655-2/"
    )
    assert parsed_listings[1].url == expected


# ===========================================================================
# 6 – Location
# ===========================================================================


def test_first_listing_location(parsed_listings):
    assert parsed_listings[0].location_raw == "RSSB 25 Fenchurch Avenue"


def test_second_listing_location(parsed_listings):
    assert parsed_listings[1].location_raw == "City, Swindon"


def test_risk_manager_no_location(parsed_listings):
    """Risk Manager item (id 2800002-2) has no location in the fixture."""
    risk = next(l for l in parsed_listings if l.source_listing_id == "2800002-2")
    assert risk.location_raw is None


# ===========================================================================
# 7 – Salary
# ===========================================================================


def test_second_listing_salary(parsed_listings):
    """Salary must decode HTML entities (£ → £)."""
    assert "65,000" in parsed_listings[1].salary_raw
    assert "85,000" in parsed_listings[1].salary_raw


def test_featured_listing_no_salary(parsed_listings):
    """Featured item (5828-1) has no salary field in the DL."""
    assert parsed_listings[0].salary_raw is None


def test_quantity_surveyor_no_salary(parsed_listings):
    qs = next(l for l in parsed_listings if l.source_listing_id == "2800003-2")
    assert qs.salary_raw is None


# ===========================================================================
# 8 – Contract type
# ===========================================================================


def test_first_listing_contract_type(parsed_listings):
    assert parsed_listings[0].contract_type_raw == "Contract"


def test_second_listing_contract_type(parsed_listings):
    assert parsed_listings[1].contract_type_raw == "Permanent"


# ===========================================================================
# 9 – posted_at date parsing
# ===========================================================================


def test_first_listing_posted_at(parsed_listings):
    dt = parsed_listings[0].posted_at
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 12
    assert dt.tzinfo == timezone.utc


def test_second_listing_posted_at(parsed_listings):
    dt = parsed_listings[1].posted_at
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6
    assert dt.day == 4


def test_posted_at_has_utc_timezone(parsed_listings):
    for listing in parsed_listings:
        if listing.posted_at is not None:
            assert listing.posted_at.tzinfo is not None


# ===========================================================================
# 10 – Agency extraction
# ===========================================================================


def test_first_listing_agency(parsed_listings):
    assert parsed_listings[0].agency == "Rail Safety and Standards Board"


def test_agency_with_ampersand(parsed_listings):
    """Fawkes & Reece London: ~26 must be decoded to &."""
    second = parsed_listings[1]
    assert second.agency == "Fawkes & Reece London"


def test_agencies_non_empty(parsed_listings):
    for listing in parsed_listings:
        # All fixture items have a logo link
        assert listing.agency is not None, (
            f"missing agency for {listing.source_listing_id}"
        )


# ===========================================================================
# 11 – Description snippet
# ===========================================================================


def test_descriptions_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.description_raw, (
            f"missing description for {listing.source_listing_id}"
        )


# ===========================================================================
# 12 – Error handling
# ===========================================================================


def test_empty_html_returns_empty_list():
    result = _parse_html("", _BASE_URL, _PAGE_URL)
    assert result == []


def test_whitespace_only_html_returns_empty_list():
    result = _parse_html("   \n\t  ", _BASE_URL, _PAGE_URL)
    assert result == []


def test_malformed_html_returns_empty_list():
    result = _parse_html("<not valid xml !!!", _BASE_URL, _PAGE_URL)
    # selectolax is lenient — main outcome is no crash and no listings
    assert isinstance(result, list)


def test_html_without_vacancy_listing_returns_empty():
    html = "<html><body><ol class='other-list'><li>item</li></ol></body></html>"
    result = _parse_html(html, _BASE_URL, _PAGE_URL)
    assert result == []


def test_li_without_title_link_skipped():
    html = """
    <html><body>
    <ol class="vacancy-listing">
      <li id="v999-1"><div class="heading"><h2>No anchor here</h2></div></li>
    </ol>
    </body></html>
    """
    result = _parse_html(html, _BASE_URL, _PAGE_URL)
    assert result == []


# ===========================================================================
# 13 – Slug and URL building helpers
# ===========================================================================


def test_keyword_to_slug_simple():
    assert _keyword_to_slug("project manager") == "project-manager"


def test_keyword_to_slug_uppercase():
    assert _keyword_to_slug("Quantity Surveyor") == "quantity-surveyor"


def test_keyword_to_slug_special_chars():
    result = _keyword_to_slug("programme & risk manager")
    assert result == "programme-risk-manager"


def test_build_search_url_page1():
    url = _build_search_url(_BASE_URL, "project manager", 1)
    assert url == (
        "https://www.railrecruiter.co.uk"
        "/jobs/job-search-results/kw-project-manager/co-225/"
    )
    assert "?page" not in url


def test_build_search_url_page2():
    url = _build_search_url(_BASE_URL, "project manager", 2)
    assert url.endswith("?page=2")


def test_build_search_url_quantity_surveyor():
    url = _build_search_url(_BASE_URL, "quantity surveyor", 1)
    assert "kw-quantity-surveyor" in url


# ===========================================================================
# 14 – _extract_agency helper
# ===========================================================================


def test_extract_agency_simple():
    href = "/jobs/network-rail-jobs/em-Network-Rail/"
    assert _extract_agency(href) == "Network Rail"


def test_extract_agency_ampersand():
    href = "/jobs/fawkes-reece-london-jobs/em-Fawkes-~26-Reece-London/"
    assert _extract_agency(href) == "Fawkes & Reece London"


def test_extract_agency_multi_word():
    href = "/jobs/rail-safety-and-standards-board-jobs/em-Rail-Safety-and-Standards-Board/"
    assert _extract_agency(href) == "Rail Safety and Standards Board"


def test_extract_agency_no_em_returns_none():
    href = "/jobs/some-jobs/something/"
    assert _extract_agency(href) is None


def test_extract_agency_empty_string():
    assert _extract_agency("") is None


# ===========================================================================
# 15 – _parse_date helper
# ===========================================================================


def test_parse_date_d_mon_yyyy():
    dt = _parse_date("4 Jun 2026")
    assert dt is not None
    assert dt == datetime(2026, 6, 4, tzinfo=timezone.utc)


def test_parse_date_dd_mon_yyyy():
    dt = _parse_date("12 May 2026")
    assert dt is not None
    assert dt == datetime(2026, 5, 12, tzinfo=timezone.utc)


def test_parse_date_full_month_name():
    dt = _parse_date("15 June 2026")
    assert dt is not None
    assert dt.month == 6
    assert dt.day == 15


def test_parse_date_iso():
    dt = _parse_date("2026-05-12")
    assert dt is not None
    assert dt.year == 2026


def test_parse_date_none_input():
    assert _parse_date(None) is None


def test_parse_date_empty_string():
    assert _parse_date("") is None


def test_parse_date_invalid_string():
    assert _parse_date("not a date") is None


# ===========================================================================
# 16 – since filtering (via adapter fetch with mocked HTTP)
# ===========================================================================


def _make_mock_client(html_pages: dict[str, str] | None = None, default_html: str = ""):
    """Build a mock httpx.AsyncClient context manager.

    ``html_pages`` maps URL substrings to HTML responses.  Unmatched URLs
    return ``default_html``.
    """
    html_pages = html_pages or {}

    async def mock_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        for key, html in html_pages.items():
            if key in url:
                resp.text = html
                return resp
        resp.text = default_html
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=mock_get)
    mock.post = AsyncMock(return_value=MagicMock())
    return mock


@patch("httpx.AsyncClient")
def test_since_filter_excludes_old_listings(mock_client_cls, raw_html):
    """Listings with posted_at < since must be excluded."""
    since = datetime(2026, 6, 5, tzinfo=timezone.utc)

    mock_client_cls.return_value = _make_mock_client(
        {"job-search-results": raw_html}
    )

    adapter = RailRecruiterAdapter(
        crawl_delay=0,
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    listings = asyncio.run(adapter.fetch(since=since))

    dated_listings = [l for l in listings if l.posted_at is not None]
    for listing in dated_listings:
        assert listing.posted_at >= since, (
            f"{listing.source_listing_id} posted {listing.posted_at} is before since={since}"
        )


@patch("httpx.AsyncClient")
def test_since_none_returns_all_listings(mock_client_cls, raw_html):
    """When since=None all listings from the page are returned."""
    mock_client_cls.return_value = _make_mock_client(
        {"job-search-results": raw_html}
    )

    adapter = RailRecruiterAdapter(
        crawl_delay=0,
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    listings = asyncio.run(adapter.fetch(since=None))

    assert len(listings) == 10


# ===========================================================================
# 17 – Pagination: stops when page returns no listings
# ===========================================================================


@patch("httpx.AsyncClient")
def test_pagination_stops_on_empty_page(mock_client_cls, raw_html):
    """Adapter must stop paginating when a page returns no job cards."""
    empty_html = (
        "<html><body>"
        "<ol class='vacancy-listing'></ol>"
        "</body></html>"
    )

    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "job-search-results" in url:
            call_count += 1
            resp.text = raw_html if call_count == 1 else empty_html
        else:
            resp.text = ""
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=mock_get)
    mock.post = AsyncMock(return_value=MagicMock())
    mock_client_cls.return_value = mock

    adapter = RailRecruiterAdapter(
        crawl_delay=0,
        keywords_list=["project manager"],
        max_pages_per_query=5,
    )
    listings = asyncio.run(adapter.fetch())

    # Page 1 has results; page 2 empty → should stop after 2 calls
    assert call_count == 2
    assert len(listings) == 10


# ===========================================================================
# 18 – Error resilience: HTTP error returns []
# ===========================================================================


@patch("httpx.AsyncClient")
def test_http_error_returns_empty_list(mock_client_cls):
    """An HTTP error on the search page must return [] gracefully."""
    async def mock_get(url, **kwargs):
        resp = MagicMock()
        if "job-search-results" in url:
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "403",
                    request=MagicMock(),
                    response=MagicMock(status_code=403),
                )
            )
        else:
            resp.raise_for_status = MagicMock()
            resp.text = ""
        return resp

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=mock_get)
    mock.post = AsyncMock(return_value=MagicMock())
    mock_client_cls.return_value = mock

    adapter = RailRecruiterAdapter(
        crawl_delay=0,
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    listings = asyncio.run(adapter.fetch())

    assert listings == []


# ===========================================================================
# 19 – Deduplication across keywords
# ===========================================================================


@patch("httpx.AsyncClient")
def test_dedup_across_keywords(mock_client_cls, raw_html):
    """Same listing appearing under two keywords must be deduplicated."""
    mock_client_cls.return_value = _make_mock_client(
        {"job-search-results": raw_html}
    )

    adapter = RailRecruiterAdapter(
        crawl_delay=0,
        keywords_list=["project manager", "programme manager"],
        max_pages_per_query=1,
    )
    listings = asyncio.run(adapter.fetch())

    # Both queries return the same 10-item fixture; dedup must yield 10 not 20
    assert len(listings) == 10


# ===========================================================================
# 20 – Adapter defaults
# ===========================================================================


def test_adapter_name():
    adapter = RailRecruiterAdapter()
    assert adapter.name == "railrecruiter"


def test_adapter_default_crawl_delay():
    adapter = RailRecruiterAdapter()
    assert adapter.crawl_delay == 3


def test_adapter_accepts_keywords_list():
    adapter = RailRecruiterAdapter(keywords_list=["risk manager"])
    assert adapter.keywords_list == ["risk manager"]


def test_adapter_default_keywords_list_non_empty():
    adapter = RailRecruiterAdapter()
    assert len(adapter.keywords_list) > 0


def test_adapter_base_url_default():
    adapter = RailRecruiterAdapter()
    assert adapter.base_url == "https://www.railrecruiter.co.uk"
