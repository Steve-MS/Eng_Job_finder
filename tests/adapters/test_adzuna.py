"""Fixture-driven unit tests for the Adzuna API adapter parse function.

Loads the saved API response snapshot from:
    tests/fixtures/adapters/adzuna_page1.json

and asserts that _parse_page() populates the expected fields.

Fixture was captured 2026-06-15 against the live Adzuna API:
    endpoint: /v1/api/jobs/gb/search/1
    params:   results_per_page=5, what=project manager mechanical engineering,
              contract=1, sort_by=date
app_id and app_key have been scrubbed from all URL strings in the fixture.

Expected schema (per Adzuna free-tier API):
    id             → source_listing_id (string)
    title          → title
    company.display_name → employer
    location.display_name → location_raw
    redirect_url   → url
    created (ISO-8601 UTC) → posted_at
    salary_min/salary_max → salary_raw (may be None)
    salary_is_predicted   → "(predicted)" suffix when "1"
    contract_type  → contract_type_raw ("contract" for all in fixture)
    description    → description_raw
"""
from __future__ import annotations

import json
import pathlib
from datetime import timezone

import pytest

from mechpm.adapters.adzuna import _parse_page

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "adapters"
    / "adzuna_page1.json"
)


@pytest.fixture(scope="module")
def fixture_data() -> list[dict]:
    """Load the adzuna_page1.json fixture and return the results list."""
    raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    return raw["results"]


@pytest.fixture(scope="module")
def parsed_listings(fixture_data):
    """Run _parse_page() over the fixture results list."""
    return _parse_page(fixture_data)


# ---------------------------------------------------------------------------
# Volume gate
# ---------------------------------------------------------------------------

def test_listing_count_minimum(parsed_listings):
    """Fixture page must yield at least 3 listings."""
    assert len(parsed_listings) >= 3, (
        f"Expected >= 3 listings from fixture, got {len(parsed_listings)}"
    )


# ---------------------------------------------------------------------------
# Source identifier
# ---------------------------------------------------------------------------

def test_source_is_adzuna(parsed_listings):
    """Every listing must have source == 'adzuna'."""
    for listing in parsed_listings:
        assert listing.source == "adzuna", (
            f"Expected source='adzuna', got {listing.source!r}"
        )


# ---------------------------------------------------------------------------
# ID extraction
# ---------------------------------------------------------------------------

def test_source_listing_id_populated(parsed_listings):
    """Every listing must have a non-empty source_listing_id."""
    for listing in parsed_listings:
        assert listing.source_listing_id, (
            f"source_listing_id is empty for title={listing.title!r}"
        )


def test_source_listing_id_numeric(parsed_listings):
    """Adzuna IDs are numeric strings."""
    for listing in parsed_listings:
        assert listing.source_listing_id.isdigit(), (
            f"source_listing_id {listing.source_listing_id!r} is not numeric"
        )


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

def test_title_populated_for_all(parsed_listings):
    """Every listing must have a non-empty title."""
    for listing in parsed_listings:
        assert listing.title, f"title is empty for id={listing.source_listing_id!r}"


# ---------------------------------------------------------------------------
# Employer
# ---------------------------------------------------------------------------

def test_employer_populated_for_all(parsed_listings):
    """Every listing must have an employer (Adzuna always includes company.display_name)."""
    for listing in parsed_listings:
        assert listing.employer, (
            f"employer is None/empty for id={listing.source_listing_id!r} "
            f"title={listing.title!r}"
        )


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------

def test_url_populated_for_all(parsed_listings):
    """Every listing must have a non-empty url."""
    for listing in parsed_listings:
        assert listing.url, f"url is empty for id={listing.source_listing_id!r}"


def test_url_is_adzuna_domain(parsed_listings):
    """All redirect_urls must point to adzuna.co.uk."""
    for listing in parsed_listings:
        assert "adzuna.co.uk" in listing.url, (
            f"url does not look like an Adzuna URL: {listing.url!r}"
        )


def test_url_contains_no_secrets(parsed_listings):
    """Fixture URLs must not contain real app_id or app_key values (scrub check)."""
    for listing in parsed_listings:
        # The fixture scrub replaced real keys with REDACTED_APP_ID / REDACTED_APP_KEY
        # Real keys are 8 chars (app_id) and 32 chars (app_key).
        # We check that utm_source is either absent or REDACTED.
        if "utm_source=" in listing.url:
            assert "REDACTED" in listing.url or len(
                listing.url.split("utm_source=")[-1].split("&")[0]
            ) != 8, (
                "url may contain a real app_id — fixture not properly scrubbed"
            )


# ---------------------------------------------------------------------------
# posted_at
# ---------------------------------------------------------------------------

def test_posted_at_populated_for_all(parsed_listings):
    """Every listing must have posted_at parsed from the 'created' field."""
    for listing in parsed_listings:
        assert listing.posted_at is not None, (
            f"posted_at is None for id={listing.source_listing_id!r}"
        )


def test_posted_at_timezone_aware(parsed_listings):
    """posted_at must be a UTC-aware datetime."""
    for listing in parsed_listings:
        if listing.posted_at:
            assert listing.posted_at.tzinfo is not None, (
                f"posted_at must be tz-aware for id={listing.source_listing_id!r}"
            )
            assert listing.posted_at.tzinfo == timezone.utc


def test_posted_at_plausible_year(parsed_listings):
    """Parsed dates must fall within a plausible range (2025–2027)."""
    for listing in parsed_listings:
        if listing.posted_at:
            assert 2025 <= listing.posted_at.year <= 2027, (
                f"posted_at year {listing.posted_at.year} out of range for "
                f"id={listing.source_listing_id!r}"
            )


# ---------------------------------------------------------------------------
# salary_raw
# ---------------------------------------------------------------------------

def test_salary_raw_populated_for_some(parsed_listings):
    """At least one listing in the fixture must have a salary_raw value."""
    populated = [lst for lst in parsed_listings if lst.salary_raw]
    assert populated, (
        "No listings have salary_raw — Adzuna fixture expected to carry salary data"
    )


def test_salary_raw_format_pound_sign(parsed_listings):
    """Populated salary_raw strings must start with '£'."""
    for listing in parsed_listings:
        if listing.salary_raw:
            assert listing.salary_raw.startswith("£"), (
                f"salary_raw does not start with '£': {listing.salary_raw!r}"
            )


def test_salary_predicted_suffix(fixture_data, parsed_listings):
    """Listings with salary_is_predicted=='1' must have '(predicted)' in salary_raw."""
    for raw, listing in zip(fixture_data, parsed_listings):
        if raw.get("salary_is_predicted") == "1" and listing.salary_raw:
            assert "(predicted)" in listing.salary_raw, (
                f"Expected '(predicted)' in salary_raw for predicted listing "
                f"id={listing.source_listing_id!r}, got {listing.salary_raw!r}"
            )


def test_salary_raw_none_when_no_salary(fixture_data, parsed_listings):
    """Listings with no salary_min and no salary_max must have salary_raw=None."""
    for raw, listing in zip(fixture_data, parsed_listings):
        if raw.get("salary_min") is None and raw.get("salary_max") is None:
            assert listing.salary_raw is None, (
                f"Expected salary_raw=None for listing with no salary data, "
                f"got {listing.salary_raw!r}"
            )


# ---------------------------------------------------------------------------
# contract_type_raw
# ---------------------------------------------------------------------------

def test_contract_type_is_contract_for_all(parsed_listings):
    """All listings from the contract=1 query filter must have contract_type_raw=='contract'."""
    for listing in parsed_listings:
        assert listing.contract_type_raw == "contract", (
            f"Expected contract_type_raw='contract', got "
            f"{listing.contract_type_raw!r} for id={listing.source_listing_id!r}"
        )


# ---------------------------------------------------------------------------
# description_raw
# ---------------------------------------------------------------------------

def test_description_raw_populated_for_some(parsed_listings):
    """At least some listings should carry a description snippet."""
    populated = [lst for lst in parsed_listings if lst.description_raw]
    assert populated, "No listings have description_raw from fixture"


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------

def test_metadata_salary_annualised_flag(parsed_listings):
    """metadata must carry salary_annualised=True so Ada knows to convert."""
    for listing in parsed_listings:
        assert listing.metadata.get("salary_annualised") is True, (
            f"metadata.salary_annualised should be True for id={listing.source_listing_id!r}"
        )


def test_metadata_category_label_populated(parsed_listings):
    """metadata.category_label should be populated (Adzuna always returns category)."""
    populated = [lst for lst in parsed_listings if lst.metadata.get("category_label")]
    assert populated, "No listings have metadata.category_label from fixture"


# ---------------------------------------------------------------------------
# Field population rates
# ---------------------------------------------------------------------------

def test_field_population_rates(parsed_listings):
    """Critical fields must hit 100% population threshold for the fixture page."""
    n = len(parsed_listings)
    assert n >= 3, f"Too few listings to assess rates: {n}"

    def rate(field: str) -> float:
        return sum(1 for lst in parsed_listings if getattr(lst, field)) / n

    assert rate("title") == 1.0, f"title rate {rate('title'):.0%} < 100%"
    assert rate("source_listing_id") == 1.0, f"id rate {rate('source_listing_id'):.0%} < 100%"
    assert rate("employer") == 1.0, f"employer rate {rate('employer'):.0%} < 100%"
    assert rate("url") == 1.0, f"url rate {rate('url'):.0%} < 100%"
    assert rate("posted_at") == 1.0, f"posted_at rate {rate('posted_at'):.0%} < 100%"


# ---------------------------------------------------------------------------
# M3: AdzunaAdapter constructor — what_or / what_exclude params
# ---------------------------------------------------------------------------

class TestAdzunaAdapterParams:
    """Tests for what_or / what_exclude / location0 / category (M3)."""

    def test_adapter_stores_what_or(self):
        """AdzunaAdapter must store what_or on the instance."""
        from mechpm.adapters.adzuna import AdzunaAdapter

        adapter = AdzunaAdapter(
            app_id="test_id",
            app_key="test_key",
            what_or="project manager mechanical HVAC",
        )
        assert adapter.what_or == "project manager mechanical HVAC"

    def test_adapter_stores_what_exclude(self):
        """AdzunaAdapter must store what_exclude on the instance."""
        from mechpm.adapters.adzuna import AdzunaAdapter

        adapter = AdzunaAdapter(
            app_id="test_id",
            app_key="test_key",
            what_exclude="software devops cloud",
        )
        assert adapter.what_exclude == "software devops cloud"

    def test_adapter_stores_location0(self):
        """AdzunaAdapter must store location0 on the instance."""
        from mechpm.adapters.adzuna import AdzunaAdapter

        adapter = AdzunaAdapter(app_id="id", app_key="key", location0="UK")
        assert adapter.location0 == "UK"

    def test_adapter_stores_category(self):
        """AdzunaAdapter must store category on the instance."""
        from mechpm.adapters.adzuna import AdzunaAdapter

        adapter = AdzunaAdapter(app_id="id", app_key="key", category="engineering-jobs")
        assert adapter.category == "engineering-jobs"

    def test_adapter_stores_max_pages(self):
        """AdzunaAdapter must honour the max_pages constructor param."""
        from mechpm.adapters.adzuna import AdzunaAdapter

        adapter = AdzunaAdapter(app_id="id", app_key="key", max_pages=4)
        assert adapter.max_pages == 4

    def test_adapter_defaults_backward_compat(self):
        """AdzunaAdapter constructed without new params must not break."""
        from mechpm.adapters.adzuna import AdzunaAdapter

        adapter = AdzunaAdapter(app_id="id", app_key="key")
        assert adapter.what_or == ""
        assert adapter.what_exclude == ""
        assert adapter.location0 == "UK"
        assert adapter.category == ""
