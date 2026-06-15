"""Fixture-driven unit tests for the StepStone adapter (Totaljobs + CWJobs).

Loads saved HTML snapshots from tests/fixtures/adapters/{totaljobs,cwjobs}_page1.html
and asserts that StepStoneAdapter._parse_page() populates the expected fields.

Calibrated 2026-06-15 against live pages. Key findings recorded here:
  - Old path /jobs/project-manager/engineering-jobs → HTTP 500.
  - Working path: /jobs/project-manager/in-uk?contract=true (both sites).
  - Salary selector updated: job-item-salary → job-item-salary-info.
  - Date selector updated: job-item-date → job-item-timeago (wraps <time>).
  - Emotion CSS-in-JS <style> blocks stripped before parsing to avoid text pollution.
  - CWJobs listing URLs resolve to totaljobs.com domain (shared StepStone backend).
  - Both sites return 25 cards per page on this search.
"""
from __future__ import annotations

import pathlib
from datetime import timezone

import pytest

from mechpm.adapters.stepstone import StepStoneAdapter

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "adapters"
_TJ_FIXTURE = _FIXTURE_DIR / "totaljobs_page1.html"
_CW_FIXTURE = _FIXTURE_DIR / "cwjobs_page1.html"

_TJ_DOMAIN = "www.totaljobs.com"
_CW_DOMAIN = "www.cwjobs.co.uk"
_TJ_BASE = f"https://{_TJ_DOMAIN}"

# Minimum listings we expect from either fixture page (page had 25 live).
_MIN_LISTINGS = 10


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def totaljobs_adapter() -> StepStoneAdapter:
    return StepStoneAdapter(
        name="totaljobs",
        domain=_TJ_DOMAIN,
        search_path="/jobs/project-manager/in-uk",
        crawl_delay=3,
    )


@pytest.fixture(scope="module")
def cwjobs_adapter() -> StepStoneAdapter:
    return StepStoneAdapter(
        name="cwjobs",
        domain=_CW_DOMAIN,
        search_path="/jobs/project-manager/in-uk",
        crawl_delay=3,
    )


@pytest.fixture(scope="module")
def totaljobs_listings(totaljobs_adapter):
    html = _TJ_FIXTURE.read_text(encoding="utf-8")
    return totaljobs_adapter._parse_page(html, 1)


@pytest.fixture(scope="module")
def cwjobs_listings(cwjobs_adapter):
    html = _CW_FIXTURE.read_text(encoding="utf-8")
    return cwjobs_adapter._parse_page(html, 1)


# ---------------------------------------------------------------------------
# Totaljobs — count & source name
# ---------------------------------------------------------------------------

def test_totaljobs_min_listings(totaljobs_listings):
    assert len(totaljobs_listings) >= _MIN_LISTINGS, (
        f"Expected >= {_MIN_LISTINGS} listings, got {len(totaljobs_listings)}"
    )


def test_totaljobs_source_field(totaljobs_listings):
    for listing in totaljobs_listings:
        assert listing.source == "totaljobs", (
            f"Expected source='totaljobs', got {listing.source!r}"
        )


# ---------------------------------------------------------------------------
# Totaljobs — first card spot-check
# ---------------------------------------------------------------------------

def test_totaljobs_first_card(totaljobs_listings):
    """Spot-check: first card is Creative Support PM from Blackpool."""
    l = totaljobs_listings[0]
    assert l.source_listing_id == "107524550"
    assert l.title == "Project Manager"
    assert l.employer == "Creative Support"
    assert l.location_raw == "Blackpool, Lancashire"
    assert l.url == f"{_TJ_BASE}/job/project-manager/creative-support-job107524550"
    assert l.contract_type_raw == "contract"


def test_totaljobs_first_card_salary(totaljobs_listings):
    """Salary from job-item-salary-info must be populated for first card."""
    l = totaljobs_listings[0]
    assert l.salary_raw is not None, "salary_raw must not be None for first card"
    assert "14.75" in l.salary_raw, (
        f"Expected '14.75' in salary_raw, got {l.salary_raw!r}"
    )


def test_totaljobs_first_card_posted_at(totaljobs_listings):
    """posted_at must be a UTC-aware datetime parsed from relative date."""
    l = totaljobs_listings[0]
    assert l.posted_at is not None, "posted_at must be parsed from '2 days ago'"
    assert l.posted_at.tzinfo is not None, "posted_at must be timezone-aware"
    assert l.posted_at.tzinfo == timezone.utc


def test_totaljobs_title_no_css_pollution(totaljobs_listings):
    """Title must not contain CSS text (Emotion style stripping check)."""
    for listing in totaljobs_listings:
        assert "{" not in listing.title, (
            f"CSS leak in title: {listing.title!r}"
        )
        assert "box-sizing" not in listing.title, (
            f"CSS leak in title: {listing.title!r}"
        )


def test_totaljobs_employer_no_css_pollution(totaljobs_listings):
    """Employer must not contain CSS text."""
    for listing in totaljobs_listings:
        if listing.employer:
            assert "{" not in listing.employer, (
                f"CSS leak in employer: {listing.employer!r}"
            )
            assert "box-sizing" not in listing.employer, (
                f"CSS leak in employer: {listing.employer!r}"
            )


# ---------------------------------------------------------------------------
# Totaljobs — field population rates (acceptance gate)
# ---------------------------------------------------------------------------

def test_totaljobs_field_population_rates(totaljobs_listings):
    """≥90% of listings must have title, employer, location, url populated."""
    n = len(totaljobs_listings)
    assert n > 0, "Fixture produced no listings"

    def rate(field: str) -> float:
        return sum(1 for l in totaljobs_listings if getattr(l, field)) / n

    assert rate("title") >= 0.9, f"title rate {rate('title'):.1%} < 90%"
    assert rate("employer") >= 0.9, f"employer rate {rate('employer'):.1%} < 90%"
    assert rate("location_raw") >= 0.9, f"location_raw rate {rate('location_raw'):.1%} < 90%"
    assert rate("url") >= 0.9, f"url rate {rate('url'):.1%} < 90%"
    assert rate("salary_raw") >= 0.5, f"salary_raw rate {rate('salary_raw'):.1%} < 50%"


# ---------------------------------------------------------------------------
# CWJobs — count & source name
# ---------------------------------------------------------------------------

def test_cwjobs_min_listings(cwjobs_listings):
    assert len(cwjobs_listings) >= _MIN_LISTINGS, (
        f"Expected >= {_MIN_LISTINGS} listings, got {len(cwjobs_listings)}"
    )


def test_cwjobs_source_field(cwjobs_listings):
    for listing in cwjobs_listings:
        assert listing.source == "cwjobs", (
            f"Expected source='cwjobs', got {listing.source!r}"
        )


# ---------------------------------------------------------------------------
# CWJobs — first card spot-check
# ---------------------------------------------------------------------------

def test_cwjobs_first_card(cwjobs_listings):
    """Spot-check: first CWJobs card resolves to totaljobs.com URL."""
    l = cwjobs_listings[0]
    assert l.source_listing_id == "107478082"
    assert l.title == "Correspondence Project Manager"
    assert l.employer == "Keystream Group Limited"
    # CWJobs listing URLs resolve to totaljobs.com (shared StepStone backend).
    assert "totaljobs.com" in l.url, (
        f"Expected CWJobs URL to point to totaljobs.com, got {l.url!r}"
    )
    assert l.contract_type_raw == "contract"


def test_cwjobs_title_no_css_pollution(cwjobs_listings):
    for listing in cwjobs_listings:
        assert "{" not in listing.title, f"CSS leak in title: {listing.title!r}"


def test_cwjobs_employer_no_css_pollution(cwjobs_listings):
    for listing in cwjobs_listings:
        if listing.employer:
            assert "{" not in listing.employer, (
                f"CSS leak in employer: {listing.employer!r}"
            )


# ---------------------------------------------------------------------------
# CWJobs — field population rates (acceptance gate)
# ---------------------------------------------------------------------------

def test_cwjobs_field_population_rates(cwjobs_listings):
    """≥90% of listings must have title, employer, location, url populated."""
    n = len(cwjobs_listings)
    assert n > 0, "Fixture produced no listings"

    def rate(field: str) -> float:
        return sum(1 for l in cwjobs_listings if getattr(l, field)) / n

    assert rate("title") >= 0.9, f"title rate {rate('title'):.1%} < 90%"
    assert rate("employer") >= 0.9, f"employer rate {rate('employer'):.1%} < 90%"
    assert rate("location_raw") >= 0.9, f"location_raw rate {rate('location_raw'):.1%} < 90%"
    assert rate("url") >= 0.9, f"url rate {rate('url'):.1%} < 90%"
    assert rate("salary_raw") >= 0.5, f"salary_raw rate {rate('salary_raw'):.1%} < 50%"


# ---------------------------------------------------------------------------
# Cross-source: shared selector validation
# ---------------------------------------------------------------------------

def test_both_sources_contract_type(totaljobs_listings, cwjobs_listings):
    """contract_type_raw defaults to 'contract' on both sources."""
    for listing in totaljobs_listings + cwjobs_listings:
        assert listing.contract_type_raw == "contract", (
            f"[{listing.source}] contract_type_raw={listing.contract_type_raw!r}"
        )


def test_both_sources_source_listing_id_numeric(totaljobs_listings, cwjobs_listings):
    """source_listing_id must be a non-empty numeric string for every listing."""
    for listing in totaljobs_listings + cwjobs_listings:
        assert listing.source_listing_id, "source_listing_id must be non-empty"
        assert listing.source_listing_id.isdigit() or len(listing.source_listing_id) > 5, (
            f"[{listing.source}] unexpected source_listing_id={listing.source_listing_id!r}"
        )
