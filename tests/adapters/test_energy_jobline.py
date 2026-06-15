"""Fixture-driven unit tests for the Energy Jobline adapter parse function.

Loads the saved HTML snapshot from:
    tests/fixtures/adapters/energy_jobline_page1.html

and asserts that _parse_html() populates the expected fields.

Confirmed DOM structure (live recon 2026-06-15):
  article.node--job-per-template     → job card
  article id attr "node-{nid}"       → source_listing_id
  h2.node__title a                   → title + url (absolute href)
  .recruiter-company-profile-job-organization a → employer
  .location span                     → location_raw (optional, ~60% populated)
  .date text "MM/DD/YYYY,"           → posted_at (US date format, strip comma)
  no salary in search cards          → salary_raw always None
  contract_type_raw = "contract"     (search filter baked into URL)
"""
from __future__ import annotations

import pathlib
from datetime import timezone

import pytest

from mechpm.adapters.energy_jobline import _parse_html

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "adapters"
    / "energy_jobline_page1.html"
)
_PAGE_URL = (
    "https://www.energyjobline.com/jobs"
    "?keywords=project+manager+mechanical"
    "&location=United+Kingdom"
    "&contract_type=contract"
)
_BASE_URL = "https://www.energyjobline.com"


@pytest.fixture(scope="module")
def parsed_listings():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    return _parse_html(html, _PAGE_URL)


# ---------------------------------------------------------------------------
# Volume gate
# ---------------------------------------------------------------------------

def test_listing_count(parsed_listings):
    """Fixture page must yield 20 listings (one per article card on page 1)."""
    assert len(parsed_listings) == 20


# ---------------------------------------------------------------------------
# Source identifier
# ---------------------------------------------------------------------------

def test_source_is_energy_jobline(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == "energy_jobline"


# ---------------------------------------------------------------------------
# ID extraction from article id attribute
# ---------------------------------------------------------------------------

def test_source_listing_id_numeric(parsed_listings):
    """Every listing must have a non-empty numeric string ID."""
    for listing in parsed_listings:
        assert listing.source_listing_id, "source_listing_id must be non-empty"
        assert listing.source_listing_id.isdigit(), (
            f"source_listing_id {listing.source_listing_id!r} is not numeric"
        )


def test_first_listing_id(parsed_listings):
    assert parsed_listings[0].source_listing_id == "28782419"


# ---------------------------------------------------------------------------
# Title + URL
# ---------------------------------------------------------------------------

def test_first_listing_title(parsed_listings):
    assert parsed_listings[0].title == (
        "Senior Engineers - Protection and Control in 4 Locations"
    )


def test_first_listing_url(parsed_listings):
    expected = (
        f"{_BASE_URL}/job/"
        "senior-engineers-protection-and-control-4-locations-28782419"
    )
    assert parsed_listings[0].url == expected


def test_url_is_absolute(parsed_listings):
    for listing in parsed_listings:
        assert listing.url.startswith("https://"), (
            f"url should be absolute: {listing.url!r}"
        )


# ---------------------------------------------------------------------------
# Employer
# ---------------------------------------------------------------------------

def test_first_listing_employer(parsed_listings):
    assert parsed_listings[0].employer == "Iberdrola Renewables"


# ---------------------------------------------------------------------------
# Location (optional field)
# ---------------------------------------------------------------------------

def test_second_listing_has_location(parsed_listings):
    """Second listing (id=29869644) includes a location div."""
    listing = parsed_listings[1]
    assert listing.source_listing_id == "29869644"
    assert listing.location_raw == "Binghamton, NY, USA"


def test_first_listing_location_none(parsed_listings):
    """First listing has no location div — location_raw must be None."""
    assert parsed_listings[0].location_raw is None


# ---------------------------------------------------------------------------
# Posted date (MM/DD/YYYY US format confirmed 2026-06-15)
# ---------------------------------------------------------------------------

def test_posted_at_parsed(parsed_listings):
    """Every listing must have posted_at set (date field present on all cards)."""
    for listing in parsed_listings:
        assert listing.posted_at is not None, (
            f"posted_at is None for listing {listing.source_listing_id!r}"
        )


def test_posted_at_timezone_aware(parsed_listings):
    for listing in parsed_listings:
        if listing.posted_at:
            assert listing.posted_at.tzinfo is not None, (
                f"posted_at must be tz-aware for listing {listing.source_listing_id!r}"
            )


def test_first_listing_date(parsed_listings):
    """Date '06/15/2026' must parse as 2026-06-15 (US MM/DD/YYYY format)."""
    dt = parsed_listings[0].posted_at
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6
    assert dt.day == 15


# ---------------------------------------------------------------------------
# Contract type
# ---------------------------------------------------------------------------

def test_contract_type_is_contract(parsed_listings):
    for listing in parsed_listings:
        assert listing.contract_type_raw == "contract"


# ---------------------------------------------------------------------------
# Field population rates
# ---------------------------------------------------------------------------

def test_field_population_rates(parsed_listings):
    """Critical fields must hit minimum population thresholds.

    location_raw is optional (60% expected); all others must be 100%.
    """
    n = len(parsed_listings)
    assert n > 0, "Fixture produced no listings"

    def rate(field: str) -> float:
        return sum(1 for lst in parsed_listings if getattr(lst, field)) / n

    assert rate("title") == 1.0, f"title rate {rate('title'):.0%} < 100%"
    assert rate("source_listing_id") == 1.0, f"id rate {rate('source_listing_id'):.0%} < 100%"
    assert rate("employer") == 1.0, f"employer rate {rate('employer'):.0%} < 100%"
    assert rate("url") == 1.0, f"url rate {rate('url'):.0%} < 100%"
    assert rate("posted_at") == 1.0, f"posted_at rate {rate('posted_at'):.0%} < 100%"
    # location is optional on EJL search cards — 50% minimum is acceptable
    assert rate("location_raw") >= 0.5, (
        f"location_raw rate {rate('location_raw'):.0%} unexpectedly low"
    )


# ---------------------------------------------------------------------------
# M4: multi-query URL construction and adapter constructor
# ---------------------------------------------------------------------------

class TestEnergyJoblineMultiQuery:
    """Tests for keywords_list multi-query support (M4)."""

    def test_build_ejl_search_urls_basic(self):
        """_build_ejl_search_urls must produce one URL per keyword."""
        from mechpm.adapters.energy_jobline import _build_ejl_search_urls

        urls = _build_ejl_search_urls(
            ["project manager", "engineering manager contract"],
            location="United Kingdom",
            contract_type="contract",
        )
        assert len(urls) == 2
        assert "keywords=project+manager" in urls[0]
        assert "keywords=engineering+manager+contract" in urls[1]
        for url in urls:
            assert "location=United+Kingdom" in url
            assert "contract_type=contract" in url

    def test_build_ejl_search_urls_encodes_spaces(self):
        """Spaces in keywords must be encoded as + (quote_plus convention)."""
        from mechpm.adapters.energy_jobline import _build_ejl_search_urls

        urls = _build_ejl_search_urls(["project manager HVAC"])
        assert "project+manager+HVAC" in urls[0]

    def test_adapter_accepts_keywords_list(self):
        """EnergyJoblineAdapter stores the right number of search URLs from keywords_list."""
        from mechpm.adapters.energy_jobline import EnergyJoblineAdapter

        adapter = EnergyJoblineAdapter(
            keywords_list=["kw1", "kw2", "kw3"],
            location="United Kingdom",
            contract_type="contract",
        )
        assert len(adapter.search_urls) == 3

    def test_adapter_fallback_to_default_url(self):
        """Without keywords_list, adapter uses the legacy single search URL."""
        from mechpm.adapters.energy_jobline import EnergyJoblineAdapter, _SEARCH_URL

        adapter = EnergyJoblineAdapter()
        assert adapter.search_urls == [_SEARCH_URL]

    def test_adapter_max_pages_per_query_configurable(self):
        """max_pages_per_query must be stored on the adapter instance."""
        from mechpm.adapters.energy_jobline import EnergyJoblineAdapter

        adapter = EnergyJoblineAdapter(max_pages_per_query=3)
        assert adapter.max_pages_per_query == 3
