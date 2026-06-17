"""Fixture-driven unit tests for the Phenom platform adapter.

Tests both BAM Careers and Mace Group HTML fixtures against the ``parse_html``
function exposed by ``mechpm.adapters.phenom``.

Confirmed fixture structure (live recon 2026-06-17):
  - phApp.ddo.eagerLoadRefineSearch.data.jobs[]  — embedded JSON, 10 per page
  - phApp.ddo.eagerLoadRefineSearch.totalHits    — total matching jobs
  - Pagination: ?from=N&s=1  (10 per page, N=0,10,20,...)
  - Job URL:  {base_url}/{site_path}/job/{jobSeqNo}
  - BAM: companyName="BAM Construction" embedded in job data
  - Mace: no companyName — fallback_employer used
"""
from __future__ import annotations

import pathlib
from datetime import timezone

import pytest

from mechpm.adapters.phenom import parse_html, _parse_posted_date, _build_location_raw, PhenomAdapter

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "adapters"

_BAM_FIXTURE = _FIXTURES / "bam_careers_page1.html"
_MACE_FIXTURE = _FIXTURES / "mace_group_page1.html"

_BAM_BASE_URL = "https://www.bamcareers.com"
_BAM_SITE_PATH = "/uk/en"
_BAM_EMPLOYER = "BAM Construction"
_BAM_PAGE_URL = f"{_BAM_BASE_URL}{_BAM_SITE_PATH}/search-results?keywords=project+manager"

_MACE_BASE_URL = "https://careers.macegroup.com"
_MACE_SITE_PATH = "/gb/en"
_MACE_EMPLOYER = "Mace Group"
_MACE_PAGE_URL = f"{_MACE_BASE_URL}{_MACE_SITE_PATH}/search-results?keywords=project+manager"


# ---------------------------------------------------------------------------
# Fixtures (module-scoped for speed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bam_listings():
    html = _BAM_FIXTURE.read_text(encoding="utf-8")
    return parse_html(html, _BAM_PAGE_URL, _BAM_BASE_URL, _BAM_SITE_PATH, "bam_careers", _BAM_EMPLOYER)


@pytest.fixture(scope="module")
def mace_listings():
    html = _MACE_FIXTURE.read_text(encoding="utf-8")
    return parse_html(html, _MACE_PAGE_URL, _MACE_BASE_URL, _MACE_SITE_PATH, "mace_group", _MACE_EMPLOYER)


# ===========================================================================
# BAM Careers tests
# ===========================================================================

class TestBamCareers:
    """Tests using the BAM Careers HTML fixture."""

    # -----------------------------------------------------------------------
    # T1: Volume gate
    # -----------------------------------------------------------------------

    def test_listing_count(self, bam_listings):
        """BAM fixture page must yield exactly 10 listings."""
        assert len(bam_listings) == 10

    # -----------------------------------------------------------------------
    # T2: Source identifier
    # -----------------------------------------------------------------------

    def test_source_is_bam_careers(self, bam_listings):
        for listing in bam_listings:
            assert listing.source == "bam_careers"

    # -----------------------------------------------------------------------
    # T3: source_listing_id (reqId — must be numeric string)
    # -----------------------------------------------------------------------

    def test_source_listing_ids_numeric(self, bam_listings):
        for listing in bam_listings:
            assert listing.source_listing_id, "source_listing_id must not be empty"
            assert listing.source_listing_id.isdigit(), (
                f"source_listing_id {listing.source_listing_id!r} must be numeric"
            )

    def test_first_bam_listing_id(self, bam_listings):
        assert bam_listings[0].source_listing_id == "25066"

    # -----------------------------------------------------------------------
    # T4: Title
    # -----------------------------------------------------------------------

    def test_titles_non_empty(self, bam_listings):
        for listing in bam_listings:
            assert listing.title, f"title must not be empty for {listing.source_listing_id}"

    def test_first_bam_listing_title(self, bam_listings):
        assert bam_listings[0].title == "Account Manager"

    # -----------------------------------------------------------------------
    # T5: URL (absolute, correct pattern)
    # -----------------------------------------------------------------------

    def test_urls_absolute(self, bam_listings):
        for listing in bam_listings:
            assert listing.url.startswith("https://www.bamcareers.com/uk/en/job/"), (
                f"unexpected URL pattern: {listing.url!r}"
            )

    def test_first_bam_listing_url(self, bam_listings):
        expected = "https://www.bamcareers.com/uk/en/job/BAM1GLOBAL25066EXTERNALENUK"
        assert bam_listings[0].url == expected

    # -----------------------------------------------------------------------
    # T6: Employer (embedded companyName in BAM)
    # -----------------------------------------------------------------------

    def test_bam_employer_populated(self, bam_listings):
        for listing in bam_listings:
            assert listing.employer, f"employer must be set for {listing.source_listing_id}"

    def test_first_bam_employer(self, bam_listings):
        assert bam_listings[0].employer == "BAM Construction"

    # -----------------------------------------------------------------------
    # T7: Location
    # -----------------------------------------------------------------------

    def test_first_bam_location(self, bam_listings):
        assert bam_listings[0].location_raw == "Exeter, GBR"

    # -----------------------------------------------------------------------
    # T8: Posted date
    # -----------------------------------------------------------------------

    def test_bam_posted_dates_parsed(self, bam_listings):
        for listing in bam_listings:
            assert listing.posted_at is not None, (
                f"posted_at is None for {listing.source_listing_id}"
            )

    def test_bam_posted_dates_timezone_aware(self, bam_listings):
        for listing in bam_listings:
            if listing.posted_at:
                assert listing.posted_at.tzinfo is not None

    def test_first_bam_posted_date(self, bam_listings):
        dt = bam_listings[0].posted_at
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 5

    # -----------------------------------------------------------------------
    # T9: Description teaser present
    # -----------------------------------------------------------------------

    def test_bam_description_present(self, bam_listings):
        populated = sum(1 for lst in bam_listings if lst.description_raw)
        assert populated >= 8, f"expected ≥8/10 listings with description, got {populated}"

    # -----------------------------------------------------------------------
    # T10: contract_type_raw populated from job type field
    # -----------------------------------------------------------------------

    def test_bam_contract_type_raw_present(self, bam_listings):
        populated = sum(1 for lst in bam_listings if lst.contract_type_raw)
        assert populated >= 8, (
            f"expected ≥8/10 listings with contract_type_raw, got {populated}"
        )

    def test_first_bam_contract_type(self, bam_listings):
        assert bam_listings[0].contract_type_raw == "Permanent"


# ===========================================================================
# Mace Group tests
# ===========================================================================

class TestMaceGroup:
    """Tests using the Mace Group HTML fixture."""

    # -----------------------------------------------------------------------
    # T11: Volume gate
    # -----------------------------------------------------------------------

    def test_listing_count(self, mace_listings):
        """Mace fixture page must yield exactly 10 valid listings."""
        assert len(mace_listings) == 10

    # -----------------------------------------------------------------------
    # T12: Source identifier
    # -----------------------------------------------------------------------

    def test_source_is_mace_group(self, mace_listings):
        for listing in mace_listings:
            assert listing.source == "mace_group"

    # -----------------------------------------------------------------------
    # T13: source_listing_id (numeric reqId only — "Required Id" entries skipped)
    # -----------------------------------------------------------------------

    def test_source_listing_ids_numeric(self, mace_listings):
        for listing in mace_listings:
            assert listing.source_listing_id.isdigit(), (
                f"'Required Id' placeholder must be filtered out; "
                f"got {listing.source_listing_id!r}"
            )

    def test_first_mace_listing_id(self, mace_listings):
        assert mace_listings[0].source_listing_id == "39060"

    # -----------------------------------------------------------------------
    # T14: Title
    # -----------------------------------------------------------------------

    def test_first_mace_listing_title(self, mace_listings):
        assert mace_listings[0].title == "Project Manager"

    # -----------------------------------------------------------------------
    # T15: URL
    # -----------------------------------------------------------------------

    def test_mace_urls_absolute(self, mace_listings):
        for listing in mace_listings:
            assert listing.url.startswith("https://careers.macegroup.com/gb/en/job/"), (
                f"unexpected URL: {listing.url!r}"
            )

    def test_first_mace_listing_url(self, mace_listings):
        expected = "https://careers.macegroup.com/gb/en/job/MACEGB39060EXTERNALENGB"
        assert mace_listings[0].url == expected

    # -----------------------------------------------------------------------
    # T16: Employer falls back to configured fallback_employer for Mace
    # -----------------------------------------------------------------------

    def test_mace_employer_fallback(self, mace_listings):
        """Mace job cards have no companyName — employer must be the fallback."""
        for listing in mace_listings:
            assert listing.employer == "Mace Group", (
                f"Expected fallback employer 'Mace Group', got {listing.employer!r}"
            )

    # -----------------------------------------------------------------------
    # T17: Location
    # -----------------------------------------------------------------------

    def test_first_mace_location(self, mace_listings):
        assert mace_listings[0].location_raw == "Derby, United Kingdom"

    # -----------------------------------------------------------------------
    # T18: Posted date
    # -----------------------------------------------------------------------

    def test_mace_posted_dates_parsed(self, mace_listings):
        for listing in mace_listings:
            assert listing.posted_at is not None

    def test_first_mace_posted_date(self, mace_listings):
        dt = mace_listings[0].posted_at
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 20


# ===========================================================================
# Unit tests for helper functions
# ===========================================================================

class TestParsedDate:
    """Unit tests for _parse_posted_date helper."""

    def test_iso_with_ms_and_zero_offset(self):
        dt = _parse_posted_date("2026-06-05T00:00:00.000+0000")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 5
        assert dt.tzinfo == timezone.utc

    def test_iso_with_z_suffix(self):
        dt = _parse_posted_date("2026-03-20T05:59:27.000Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 20

    def test_plain_date(self):
        dt = _parse_posted_date("2026-01-15")
        assert dt is not None
        assert dt.month == 1
        assert dt.day == 15

    def test_none_returns_none(self):
        assert _parse_posted_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_posted_date("") is None


class TestBuildLocationRaw:
    """Unit tests for _build_location_raw helper."""

    def test_uses_multi_location_list(self):
        job = {"multi_location": ["London, GBR"], "city": "London", "country": "GBR"}
        assert _build_location_raw(job) == "London, GBR"

    def test_falls_back_to_city_country(self):
        job = {"city": "Manchester", "country": "GBR"}
        assert _build_location_raw(job) == "Manchester, GBR"

    def test_city_only(self):
        job = {"city": "Bristol"}
        assert _build_location_raw(job) == "Bristol"

    def test_empty_returns_none(self):
        job = {}
        assert _build_location_raw(job) is None


class TestAdapterConstructor:
    """Tests for PhenomAdapter constructor and URL building."""

    def test_base_url(self):
        a = PhenomAdapter(
            name="bam_careers",
            domain="www.bamcareers.com",
            site_path="/uk/en",
            employer_name="BAM Construction",
        )
        assert a.base_url == "https://www.bamcareers.com"

    def test_search_url(self):
        a = PhenomAdapter(
            name="bam_careers",
            domain="www.bamcareers.com",
            site_path="/uk/en",
            employer_name="BAM Construction",
        )
        assert a.search_url == "https://www.bamcareers.com/uk/en/search-results"

    def test_page_url_offset_zero(self):
        a = PhenomAdapter(
            name="mace_group",
            domain="careers.macegroup.com",
            site_path="/gb/en",
            employer_name="Mace Group",
        )
        url = a._build_page_url("project manager", 0)
        assert url == "https://careers.macegroup.com/gb/en/search-results?keywords=project+manager"

    def test_page_url_offset_ten(self):
        a = PhenomAdapter(
            name="mace_group",
            domain="careers.macegroup.com",
            site_path="/gb/en",
            employer_name="Mace Group",
        )
        url = a._build_page_url("project manager", 10)
        assert "from=10" in url
        assert "&s=1" in url

    def test_default_keywords_list(self):
        a = PhenomAdapter(
            name="bam_careers",
            domain="www.bamcareers.com",
            site_path="/uk/en",
            employer_name="BAM",
        )
        assert len(a.keywords_list) >= 1

    def test_custom_keywords_list(self):
        a = PhenomAdapter(
            name="bam_careers",
            domain="www.bamcareers.com",
            site_path="/uk/en",
            employer_name="BAM",
            keywords_list=["project manager", "document controller"],
        )
        assert a.keywords_list == ["project manager", "document controller"]
