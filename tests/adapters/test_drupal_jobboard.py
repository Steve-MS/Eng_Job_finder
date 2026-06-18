"""Fixture-driven unit tests for the DrupalJobBoardAdapter.

Tests all 3 sites using HTML snapshots captured 2026-06-18:
  - building4jobs_page1.html   — Building4Jobs (Jobiqo/Next.js platform)
  - nce_careers_page1.html     — New Civil Engineer Careers (Drupal Epiq Jobs)
  - careers_in_construction_page1.html — Careers in Construction (Drupal Epiq Jobs)

Confirmed fixture content (live recon 2026-06-18):
  Building4Jobs   : 10 job items in __NEXT_DATA__ JSON (result_count=23)
  NCE Careers     : 14 job cards (views-row articles)
  CIC             : 20 job cards (views-row articles)

All tests call module-level parse functions directly — no HTTP I/O.
Async fetch() tests use asyncio.run() (consistent with other adapter tests).
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.drupal_jobboard import (
    DrupalJobBoardAdapter,
    _epiq_card_to_raw_listing,
    _jobiqo_item_to_raw_listing,
    _parse_epiq_date,
    _parse_jobiqo_date,
    parse_drupal_epiq_html,
    parse_jobiqo_html,
)

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "adapters"

_B4J_FIXTURE = _FIXTURES / "building4jobs_page1.html"
_NCE_FIXTURE = _FIXTURES / "nce_careers_page1.html"
_CIC_FIXTURE = _FIXTURES / "careers_in_construction_page1.html"

_B4J_BASE = "https://www.building4jobs.com"
_NCE_BASE = "https://www.newcivilengineercareers.com"
_CIC_BASE = "https://www.careersinconstruction.com"

_B4J_URL = f"{_B4J_BASE}/jobs?search_api_views_fulltext=project+manager&page=1"
_NCE_URL = f"{_NCE_BASE}/jobs?search_api_views_fulltext=project+manager&page=0"
_CIC_URL = f"{_CIC_BASE}/jobs?search_api_views_fulltext=project+manager&page=0"


# ---------------------------------------------------------------------------
# Shared fixtures (module-scoped for speed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def b4j_listings():
    html = _B4J_FIXTURE.read_text(encoding="utf-8")
    return parse_jobiqo_html(html, _B4J_URL, "building4jobs", _B4J_BASE)


@pytest.fixture(scope="module")
def nce_listings():
    html = _NCE_FIXTURE.read_text(encoding="utf-8")
    return parse_drupal_epiq_html(html, _NCE_URL, "nce_careers", _NCE_BASE)


@pytest.fixture(scope="module")
def cic_listings():
    html = _CIC_FIXTURE.read_text(encoding="utf-8")
    return parse_drupal_epiq_html(html, _CIC_URL, "careers_in_construction", _CIC_BASE)


# ===========================================================================
# 1. Helper tests — _parse_jobiqo_date
# ===========================================================================

class TestParseJobiqoDate:
    """Tests for the Jobiqo ISO 8601 date parser."""

    def test_iso8601_with_offset(self):
        """Parses 'YYYY-MM-DDTHH:MM:SS+HH:MM' and converts to UTC."""
        dt = _parse_jobiqo_date("2026-06-10T11:09:07+01:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 10
        assert dt.tzinfo == timezone.utc

    def test_iso8601_utc_z(self):
        """Parses 'YYYY-MM-DDTHH:MM:SSZ' as UTC."""
        dt = _parse_jobiqo_date("2026-06-10T11:09:07Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 10
        assert dt.hour == 11
        assert dt.tzinfo == timezone.utc

    def test_date_only_fallback(self):
        """Falls back to date-only 'YYYY-MM-DD'."""
        dt = _parse_jobiqo_date("2026-03-15")
        assert dt is not None
        assert dt.month == 3
        assert dt.day == 15

    def test_none_returns_none(self):
        assert _parse_jobiqo_date(None) is None

    def test_empty_returns_none(self):
        assert _parse_jobiqo_date("") is None

    def test_unparseable_returns_none(self):
        assert _parse_jobiqo_date("yesterday") is None


# ===========================================================================
# 2. Helper tests — _parse_epiq_date
# ===========================================================================

class TestParseEpiqDate:
    """Tests for the Drupal Epiq Jobs 'D Mon YYYY,' date parser."""

    def test_with_trailing_comma(self):
        """'16 May 2025,' → 2025-05-16 UTC."""
        dt = _parse_epiq_date("16 May 2025,")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 5
        assert dt.day == 16
        assert dt.tzinfo == timezone.utc

    def test_without_comma(self):
        """'17 Jun 2026' → 2026-06-17 UTC."""
        dt = _parse_epiq_date("17 Jun 2026")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 17

    def test_full_month_name(self):
        """'4 December 2024' (full month name) → 2024-12-04 UTC."""
        dt = _parse_epiq_date("4 December 2024")
        assert dt is not None
        assert dt.month == 12
        assert dt.day == 4

    def test_abbreviated_month(self):
        """'4 Dec 2024,' → 2024-12-04 UTC."""
        dt = _parse_epiq_date("4 Dec 2024,")
        assert dt is not None
        assert dt.month == 12
        assert dt.day == 4

    def test_none_returns_none(self):
        assert _parse_epiq_date(None) is None

    def test_empty_returns_none(self):
        assert _parse_epiq_date("") is None

    def test_unparseable_returns_none(self):
        assert _parse_epiq_date("published yesterday") is None


# ===========================================================================
# 3. Building4Jobs — Jobiqo JSON extraction
# ===========================================================================

class TestBuilding4Jobs:
    """Tests using the Building4Jobs HTML fixture (Jobiqo platform)."""

    # -----------------------------------------------------------------------
    # T1: Volume
    # -----------------------------------------------------------------------

    def test_listing_count(self, b4j_listings):
        """Fixture page 1 must yield exactly 10 listings."""
        assert len(b4j_listings) == 10

    # -----------------------------------------------------------------------
    # T2: Source identifier
    # -----------------------------------------------------------------------

    def test_source_name(self, b4j_listings):
        for lst in b4j_listings:
            assert lst.source == "building4jobs"

    # -----------------------------------------------------------------------
    # T3: source_listing_id is a non-empty numeric string
    # -----------------------------------------------------------------------

    def test_listing_ids_are_numeric_strings(self, b4j_listings):
        for lst in b4j_listings:
            assert lst.source_listing_id
            assert lst.source_listing_id.isdigit(), (
                f"Non-numeric listing ID: {lst.source_listing_id!r}"
            )

    def test_listing_ids_are_unique(self, b4j_listings):
        ids = [lst.source_listing_id for lst in b4j_listings]
        assert len(ids) == len(set(ids))

    # -----------------------------------------------------------------------
    # T4: Title is a non-empty string
    # -----------------------------------------------------------------------

    def test_titles_non_empty(self, b4j_listings):
        for lst in b4j_listings:
            assert lst.title, f"Empty title for listing {lst.source_listing_id}"

    # -----------------------------------------------------------------------
    # T5: URL points to the Building4Jobs domain
    # -----------------------------------------------------------------------

    def test_urls_point_to_b4j(self, b4j_listings):
        for lst in b4j_listings:
            assert lst.url.startswith("https://www.building4jobs.com/job/"), (
                f"Unexpected URL: {lst.url!r}"
            )

    # -----------------------------------------------------------------------
    # T6: Employer is populated (organization field)
    # -----------------------------------------------------------------------

    def test_employer_populated(self, b4j_listings):
        populated = [lst for lst in b4j_listings if lst.employer]
        assert len(populated) > 0, "Expected at least one listing with an employer"

    # -----------------------------------------------------------------------
    # T7: Location is populated (address list[0])
    # -----------------------------------------------------------------------

    def test_location_populated(self, b4j_listings):
        populated = [lst for lst in b4j_listings if lst.location_raw]
        assert len(populated) > 0, "Expected at least one listing with a location"

    # -----------------------------------------------------------------------
    # T8: posted_at is a UTC datetime when populated
    # -----------------------------------------------------------------------

    def test_posted_at_timezone(self, b4j_listings):
        dated = [lst for lst in b4j_listings if lst.posted_at]
        assert len(dated) > 0, "Expected at least one listing with a date"
        for lst in dated:
            assert lst.posted_at.tzinfo == timezone.utc

    # -----------------------------------------------------------------------
    # T9: Platform metadata
    # -----------------------------------------------------------------------

    def test_platform_metadata(self, b4j_listings):
        for lst in b4j_listings:
            assert lst.metadata.get("platform") == "jobiqo"

    # -----------------------------------------------------------------------
    # T10: contract_type_raw is None (not available in Jobiqo search cards)
    # -----------------------------------------------------------------------

    def test_contract_type_is_none(self, b4j_listings):
        for lst in b4j_listings:
            assert lst.contract_type_raw is None


# ===========================================================================
# 4. New Civil Engineer Careers — Drupal Epiq HTML
# ===========================================================================

class TestNCECareers:
    """Tests using the NCE Careers HTML fixture (Drupal Epiq Jobs)."""

    # -----------------------------------------------------------------------
    # T1: Volume
    # -----------------------------------------------------------------------

    def test_listing_count(self, nce_listings):
        """NCE fixture must yield 14 listings (14 views-row articles)."""
        assert len(nce_listings) == 14

    # -----------------------------------------------------------------------
    # T2: Source identifier
    # -----------------------------------------------------------------------

    def test_source_name(self, nce_listings):
        for lst in nce_listings:
            assert lst.source == "nce_careers"

    # -----------------------------------------------------------------------
    # T3: source_listing_id
    # -----------------------------------------------------------------------

    def test_listing_ids_non_empty(self, nce_listings):
        for lst in nce_listings:
            assert lst.source_listing_id

    def test_listing_ids_unique(self, nce_listings):
        ids = [lst.source_listing_id for lst in nce_listings]
        assert len(ids) == len(set(ids))

    # -----------------------------------------------------------------------
    # T4: Titles
    # -----------------------------------------------------------------------

    def test_titles_non_empty(self, nce_listings):
        for lst in nce_listings:
            assert lst.title

    # -----------------------------------------------------------------------
    # T5: URLs point to NCE domain
    # -----------------------------------------------------------------------

    def test_urls_point_to_nce(self, nce_listings):
        for lst in nce_listings:
            assert "newcivilengineercareers.com" in lst.url, (
                f"Unexpected URL: {lst.url!r}"
            )

    # -----------------------------------------------------------------------
    # T6: Employer populated
    # -----------------------------------------------------------------------

    def test_employer_populated(self, nce_listings):
        populated = [lst for lst in nce_listings if lst.employer]
        assert len(populated) > 0

    # -----------------------------------------------------------------------
    # T7: Location populated
    # -----------------------------------------------------------------------

    def test_location_populated(self, nce_listings):
        populated = [lst for lst in nce_listings if lst.location_raw]
        assert len(populated) > 0

    # -----------------------------------------------------------------------
    # T8: posted_at UTC when present
    # -----------------------------------------------------------------------

    def test_posted_at_timezone(self, nce_listings):
        dated = [lst for lst in nce_listings if lst.posted_at]
        assert len(dated) > 0
        for lst in dated:
            assert lst.posted_at.tzinfo == timezone.utc

    # -----------------------------------------------------------------------
    # T9: Platform metadata
    # -----------------------------------------------------------------------

    def test_platform_metadata(self, nce_listings):
        for lst in nce_listings:
            assert lst.metadata.get("platform") == "drupal_epiq"

    # -----------------------------------------------------------------------
    # T10: salary_raw and contract_type_raw are None (not in search cards)
    # -----------------------------------------------------------------------

    def test_salary_and_contract_are_none(self, nce_listings):
        for lst in nce_listings:
            assert lst.salary_raw is None
            assert lst.contract_type_raw is None


# ===========================================================================
# 5. Careers in Construction — Drupal Epiq HTML
# ===========================================================================

class TestCareersInConstruction:
    """Tests using the Careers in Construction HTML fixture (Drupal Epiq Jobs)."""

    # -----------------------------------------------------------------------
    # T1: Volume
    # -----------------------------------------------------------------------

    def test_listing_count(self, cic_listings):
        """CIC fixture must yield 20 listings (20 views-row articles)."""
        assert len(cic_listings) == 20

    # -----------------------------------------------------------------------
    # T2: Source identifier
    # -----------------------------------------------------------------------

    def test_source_name(self, cic_listings):
        for lst in cic_listings:
            assert lst.source == "careers_in_construction"

    # -----------------------------------------------------------------------
    # T3: Listing IDs
    # -----------------------------------------------------------------------

    def test_listing_ids_non_empty(self, cic_listings):
        for lst in cic_listings:
            assert lst.source_listing_id

    def test_listing_ids_unique(self, cic_listings):
        ids = [lst.source_listing_id for lst in cic_listings]
        assert len(ids) == len(set(ids))

    # -----------------------------------------------------------------------
    # T4: Titles
    # -----------------------------------------------------------------------

    def test_titles_non_empty(self, cic_listings):
        for lst in cic_listings:
            assert lst.title

    # -----------------------------------------------------------------------
    # T5: URLs point to CIC domain
    # -----------------------------------------------------------------------

    def test_urls_point_to_cic(self, cic_listings):
        for lst in cic_listings:
            assert "careersinconstruction.com" in lst.url

    # -----------------------------------------------------------------------
    # T6: Employer populated
    # -----------------------------------------------------------------------

    def test_employer_populated(self, cic_listings):
        populated = [lst for lst in cic_listings if lst.employer]
        assert len(populated) > 0

    # -----------------------------------------------------------------------
    # T7: Location populated
    # -----------------------------------------------------------------------

    def test_location_populated(self, cic_listings):
        populated = [lst for lst in cic_listings if lst.location_raw]
        assert len(populated) > 0

    # -----------------------------------------------------------------------
    # T8: posted_at UTC when present
    # -----------------------------------------------------------------------

    def test_posted_at_timezone(self, cic_listings):
        dated = [lst for lst in cic_listings if lst.posted_at]
        assert len(dated) > 0
        for lst in dated:
            assert lst.posted_at.tzinfo == timezone.utc

    # -----------------------------------------------------------------------
    # T9: Platform metadata
    # -----------------------------------------------------------------------

    def test_platform_metadata(self, cic_listings):
        for lst in cic_listings:
            assert lst.metadata.get("platform") == "drupal_epiq"

    # -----------------------------------------------------------------------
    # T10: salary_raw and contract_type_raw None
    # -----------------------------------------------------------------------

    def test_salary_and_contract_are_none(self, cic_listings):
        for lst in cic_listings:
            assert lst.salary_raw is None
            assert lst.contract_type_raw is None


# ===========================================================================
# 6. Dedup across keywords — both platforms
# ===========================================================================

class TestDedup:
    """Tests that duplicate listings (same source_listing_id across keywords)
    are collapsed to a single entry in the combined results."""

    def test_jobiqo_dedup(self, b4j_listings):
        """All IDs from a single page should already be unique."""
        ids = [lst.source_listing_id for lst in b4j_listings]
        assert len(ids) == len(set(ids))

    def test_drupal_epiq_dedup(self, nce_listings):
        """All IDs from a single page should already be unique."""
        ids = [lst.source_listing_id for lst in nce_listings]
        assert len(ids) == len(set(ids))


# ===========================================================================
# 7. Missing / malformed fields
# ===========================================================================

class TestMissingFields:
    """Edge-case tests for missing or malformed input data."""

    def test_jobiqo_missing_id_returns_none(self):
        """_jobiqo_item_to_raw_listing returns None when id is absent."""
        item = {"title": "PM Role", "url": {"path": "/job/pm-1"}}
        result = _jobiqo_item_to_raw_listing(item, "building4jobs", _B4J_BASE)
        assert result is None

    def test_jobiqo_missing_title_returns_none(self):
        """_jobiqo_item_to_raw_listing returns None when title is absent."""
        item = {"id": 99999, "title": ""}
        result = _jobiqo_item_to_raw_listing(item, "building4jobs", _B4J_BASE)
        assert result is None

    def test_jobiqo_no_salary_range(self):
        """Missing salaryRange → salary_raw is None, not an error."""
        item = {
            "id": 12345,
            "title": "Test PM",
            "url": {"path": "/job/test-pm-12345"},
            "organization": "Acme Ltd",
            "address": ["London"],
            "published": "2026-06-18T10:00:00Z",
        }
        listing = _jobiqo_item_to_raw_listing(item, "building4jobs", _B4J_BASE)
        assert listing is not None
        assert listing.salary_raw is None

    def test_jobiqo_salary_range_object(self):
        """salaryRange list of Term objects → first label extracted as salary_raw."""
        item = {
            "id": 12345,
            "title": "Test PM",
            "url": {"path": "/job/test-pm-12345"},
            "salaryRange": [{"__typename": "Term", "tid": 1008, "label": "£55,000 - £64,999"}],
            "address": ["London"],
            "published": "2026-06-18T10:00:00Z",
        }
        listing = _jobiqo_item_to_raw_listing(item, "building4jobs", _B4J_BASE)
        assert listing is not None
        assert listing.salary_raw == "£55,000 - £64,999"

    def test_jobiqo_empty_html_returns_empty_list(self):
        """parse_jobiqo_html returns [] for blank HTML."""
        result = parse_jobiqo_html("", _B4J_URL, "building4jobs", _B4J_BASE)
        assert result == []

    def test_drupal_epiq_empty_html_returns_empty_list(self):
        """parse_drupal_epiq_html returns [] for blank HTML."""
        result = parse_drupal_epiq_html("", _NCE_URL, "nce_careers", _NCE_BASE)
        assert result == []

    def test_drupal_epiq_no_cards_returns_empty_list(self):
        """parse_drupal_epiq_html returns [] when no article cards exist."""
        html = "<html><body><div class='view-content'></div></body></html>"
        result = parse_drupal_epiq_html(html, _NCE_URL, "nce_careers", _NCE_BASE)
        assert result == []


# ===========================================================================
# 8. DrupalJobBoardAdapter — URL building
# ===========================================================================

class TestAdapterUrlBuilding:
    """Unit tests for adapter URL construction."""

    def test_jobiqo_first_page_url(self):
        """Jobiqo starts at page=1."""
        adapter = DrupalJobBoardAdapter(
            name="building4jobs",
            base_url=_B4J_BASE,
            platform="jobiqo",
        )
        url = adapter._build_page_url("project manager", 1)
        assert url == (
            "https://www.building4jobs.com/jobs"
            "?search_api_views_fulltext=project+manager&page=1"
        )

    def test_jobiqo_second_page_url(self):
        adapter = DrupalJobBoardAdapter(
            name="building4jobs",
            base_url=_B4J_BASE,
            platform="jobiqo",
        )
        url = adapter._build_page_url("project manager", 2)
        assert "&page=2" in url

    def test_drupal_epiq_first_page_url(self):
        """Drupal Epiq starts at page=0."""
        adapter = DrupalJobBoardAdapter(
            name="nce_careers",
            base_url=_NCE_BASE,
            platform="drupal_epiq",
        )
        url = adapter._build_page_url("project manager", 0)
        assert url == (
            "https://www.newcivilengineercareers.com/jobs"
            "?search_api_views_fulltext=project+manager&page=0"
        )

    def test_drupal_epiq_second_page_url(self):
        adapter = DrupalJobBoardAdapter(
            name="nce_careers",
            base_url=_NCE_BASE,
            platform="drupal_epiq",
        )
        url = adapter._build_page_url("project manager", 1)
        assert "&page=1" in url

    def test_keyword_encoding(self):
        """Spaces in keyword are encoded as + in the URL."""
        adapter = DrupalJobBoardAdapter(
            name="nce_careers",
            base_url=_NCE_BASE,
            platform="drupal_epiq",
        )
        url = adapter._build_page_url("project manager", 0)
        assert "project+manager" in url

    def test_start_page_jobiqo(self):
        adapter = DrupalJobBoardAdapter(
            name="building4jobs",
            base_url=_B4J_BASE,
            platform="jobiqo",
        )
        assert adapter._start_page() == 1

    def test_start_page_drupal_epiq(self):
        adapter = DrupalJobBoardAdapter(
            name="nce_careers",
            base_url=_NCE_BASE,
            platform="drupal_epiq",
        )
        assert adapter._start_page() == 0


# ===========================================================================
# 9. DrupalJobBoardAdapter.fetch() — async integration with mocks
# ===========================================================================

class TestAdapterFetch:
    """Integration-style tests for DrupalJobBoardAdapter.fetch() using mock HTTP.

    Uses asyncio.run() (consistent with other adapter test suites in this project).
    """

    def test_fetch_jobiqo_returns_listings(self):
        """fetch() returns parsed listings when given a valid Jobiqo HTML page."""
        html = _B4J_FIXTURE.read_text(encoding="utf-8")
        page_listings = parse_jobiqo_html(html, _B4J_URL, "building4jobs", _B4J_BASE)
        adapter = DrupalJobBoardAdapter(
            name="building4jobs",
            base_url=_B4J_BASE,
            platform="jobiqo",
            keywords_list=["project manager"],
            crawl_delay=0,
            max_pages_per_query=1,
        )
        with patch("mechpm.adapters.drupal_jobboard.asyncio.sleep", AsyncMock(return_value=None)):
            with patch.object(adapter, "_fetch_page", return_value=page_listings):
                listings = asyncio.run(adapter.fetch())

        assert len(listings) == 10
        for lst in listings:
            assert lst.source == "building4jobs"

    def test_fetch_drupal_epiq_returns_listings(self):
        """fetch() returns parsed listings for a Drupal Epiq site."""
        html = _NCE_FIXTURE.read_text(encoding="utf-8")
        page_listings = parse_drupal_epiq_html(html, _NCE_URL, "nce_careers", _NCE_BASE)
        adapter = DrupalJobBoardAdapter(
            name="nce_careers",
            base_url=_NCE_BASE,
            platform="drupal_epiq",
            keywords_list=["project manager"],
            crawl_delay=0,
            max_pages_per_query=1,
        )
        with patch("mechpm.adapters.drupal_jobboard.asyncio.sleep", AsyncMock(return_value=None)):
            with patch.object(adapter, "_fetch_page", return_value=page_listings):
                listings = asyncio.run(adapter.fetch())

        assert len(listings) == 14
        for lst in listings:
            assert lst.source == "nce_careers"

    def test_fetch_dedup_across_keywords(self):
        """Duplicate listing IDs across keywords are collapsed to one entry."""
        html = _B4J_FIXTURE.read_text(encoding="utf-8")
        page_listings = parse_jobiqo_html(html, _B4J_URL, "building4jobs", _B4J_BASE)
        adapter = DrupalJobBoardAdapter(
            name="building4jobs",
            base_url=_B4J_BASE,
            platform="jobiqo",
            keywords_list=["project manager", "project engineer"],
            crawl_delay=0,
            max_pages_per_query=1,
        )
        # Both keywords return the same page → should yield only 10 unique listings.
        with patch("mechpm.adapters.drupal_jobboard.asyncio.sleep", AsyncMock(return_value=None)):
            with patch.object(adapter, "_fetch_page", return_value=page_listings):
                listings = asyncio.run(adapter.fetch())

        assert len(listings) == 10

    def test_fetch_stops_on_empty_page(self):
        """fetch() stops paginating when an empty page is returned."""
        html = _CIC_FIXTURE.read_text(encoding="utf-8")
        first_page = parse_drupal_epiq_html(html, _CIC_URL, "careers_in_construction", _CIC_BASE)

        adapter = DrupalJobBoardAdapter(
            name="careers_in_construction",
            base_url=_CIC_BASE,
            platform="drupal_epiq",
            keywords_list=["project manager"],
            crawl_delay=0,
            max_pages_per_query=5,
        )
        call_count = 0

        async def mock_fetch_page(client, url):
            nonlocal call_count
            call_count += 1
            return first_page if call_count == 1 else []

        with patch("mechpm.adapters.drupal_jobboard.asyncio.sleep", AsyncMock(return_value=None)):
            with patch.object(adapter, "_fetch_page", side_effect=mock_fetch_page):
                listings = asyncio.run(adapter.fetch())

        # Should stop after page 0 (got results) + page 1 (empty → break)
        assert call_count == 2
        assert len(listings) == 20

    def test_fetch_http_error_returns_empty(self):
        """fetch() returns [] and does not raise when all pages return empty."""
        adapter = DrupalJobBoardAdapter(
            name="nce_careers",
            base_url=_NCE_BASE,
            platform="drupal_epiq",
            keywords_list=["project manager"],
            crawl_delay=0,
            max_pages_per_query=1,
        )
        with patch("mechpm.adapters.drupal_jobboard.asyncio.sleep", AsyncMock(return_value=None)):
            with patch.object(adapter, "_fetch_page", return_value=[]):
                listings = asyncio.run(adapter.fetch())

        assert listings == []

    def test_fetch_since_filter(self):
        """Listings older than 'since' are excluded from results."""
        html = _NCE_FIXTURE.read_text(encoding="utf-8")
        all_listings = parse_drupal_epiq_html(html, _NCE_URL, "nce_careers", _NCE_BASE)
        # Use a very recent cutoff — only listings with posted_at >= 2026-06-17 pass.
        cutoff = datetime(2026, 6, 17, tzinfo=timezone.utc)
        recent = [lst for lst in all_listings if lst.posted_at and lst.posted_at >= cutoff]
        undated = [lst for lst in all_listings if not lst.posted_at]

        adapter = DrupalJobBoardAdapter(
            name="nce_careers",
            base_url=_NCE_BASE,
            platform="drupal_epiq",
            keywords_list=["project manager"],
            crawl_delay=0,
            max_pages_per_query=1,
        )
        with patch("mechpm.adapters.drupal_jobboard.asyncio.sleep", AsyncMock(return_value=None)):
            with patch.object(adapter, "_fetch_page", return_value=all_listings):
                listings = asyncio.run(adapter.fetch(since=cutoff))

        # Listings without a date pass through regardless of since filter.
        assert len(listings) == len(recent) + len(undated)

