"""Fixture-driven unit tests for the RailwayPeople adapter parse function.

Loads a saved snapshot from tests/fixtures/adapters/railwaypeople_page1.json
and asserts that _map_job_to_raw_listing() populates the expected fields.

JSON field map confirmed 2026-06-14:
  organization  → employer
  address       → location_raw (list joined with "; ")
  urlNoPrefix   → url (prefixed with base URL)
  url           → nested dict {"__typename": "Url", "path": "..."}
  published     → posted_at (ISO-8601 with tz offset)
  salaryRangeFree → salary_raw (structured; often null in search results)
  id            → source_listing_id
  title         → title
  (no description in search results)
  contract_type_raw = "Contract" (default; jobtype=contract search filter)
"""
from __future__ import annotations

import json
import pathlib
from datetime import timezone

import pytest

from mechpm.adapters.railwaypeople import _map_job_to_raw_listing

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "adapters" / "railwaypeople_page1.json"
)

_BASE_URL = "https://www.railwaypeople.com"


@pytest.fixture(scope="module")
def fixture_jobs() -> list[dict]:
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["jobs"]


@pytest.fixture(scope="module")
def parsed_listings(fixture_jobs):
    return [_map_job_to_raw_listing(job) for job in fixture_jobs]


# ---------------------------------------------------------------------------
# Per-field population tests on the first job
# ---------------------------------------------------------------------------

def test_source_is_railwaypeople(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == "railwaypeople"


def test_source_listing_id_populated(fixture_jobs, parsed_listings):
    """source_listing_id must equal str(id) for every job."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        expected = str(job["id"])
        assert listing.source_listing_id == expected, (
            f"Expected source_listing_id={expected!r}, got {listing.source_listing_id!r}"
        )


def test_title_populated(fixture_jobs, parsed_listings):
    for job, listing in zip(fixture_jobs, parsed_listings):
        assert listing.title == job["title"], (
            f"title mismatch: expected {job['title']!r}, got {listing.title!r}"
        )


def test_employer_extracted_from_organization(fixture_jobs, parsed_listings):
    """employer must be populated from the JSON 'organization' field."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        expected = job.get("organization")
        assert listing.employer == expected, (
            f"employer mismatch: expected {expected!r}, got {listing.employer!r}"
        )


def test_location_extracted_from_address(fixture_jobs, parsed_listings):
    """location_raw must be non-null and built from the address list."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        address = job.get("address", [])
        if address:
            expected = "; ".join(str(a) for a in address if a)
            assert listing.location_raw == expected, (
                f"location mismatch for job {job['id']}: "
                f"expected {expected!r}, got {listing.location_raw!r}"
            )


def test_url_built_from_urlNoPrefix(fixture_jobs, parsed_listings):
    """url must be the full canonical URL built from urlNoPrefix."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        url_no_prefix = job.get("urlNoPrefix", "")
        if url_no_prefix:
            expected = _BASE_URL + "/" + url_no_prefix.lstrip("/")
            assert listing.url == expected, (
                f"url mismatch for job {job['id']}: "
                f"expected {expected!r}, got {listing.url!r}"
            )


def test_posted_at_parsed_from_published(fixture_jobs, parsed_listings):
    """posted_at must be a UTC-aware datetime parsed from the 'published' field."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        if job.get("published"):
            assert listing.posted_at is not None, (
                f"posted_at is None for job {job['id']} (published={job['published']!r})"
            )
            assert listing.posted_at.tzinfo is not None, (
                f"posted_at must be timezone-aware for job {job['id']}"
            )


def test_contract_type_defaults_to_contract(parsed_listings):
    """contract_type_raw must default to 'Contract' for jobtype=contract search."""
    for listing in parsed_listings:
        assert listing.contract_type_raw == "Contract", (
            f"contract_type_raw={listing.contract_type_raw!r}; expected 'Contract'"
        )


# ---------------------------------------------------------------------------
# Spot-check specific well-known jobs from the fixture
# ---------------------------------------------------------------------------

def test_first_job_full_field_map(fixture_jobs, parsed_listings):
    """Spot-check job[0]: Points Operator - Peterborough."""
    job = fixture_jobs[0]
    listing = parsed_listings[0]

    assert listing.source_listing_id == "54094"
    assert listing.title == "Points Operator - Peterborough"
    assert listing.employer == "Morson Vital"
    assert listing.location_raw == "Peterborough, UK"
    assert listing.url == f"{_BASE_URL}/job/points-operator-peterborough-54094"
    assert listing.posted_at is not None
    assert listing.posted_at.year == 2026
    assert listing.posted_at.month == 6
    assert listing.posted_at.day == 3


def test_multi_location_joined(fixture_jobs, parsed_listings):
    """Job with multiple addresses must join them with '; '."""
    # Jobs 1 and 2 both have 5 addresses: Manchester, York, Leeds, Derby, London
    multi_addr_jobs = [
        (job, listing)
        for job, listing in zip(fixture_jobs, parsed_listings)
        if isinstance(job.get("address"), list) and len(job["address"]) > 1
    ]
    assert multi_addr_jobs, "Expected at least one multi-location job in fixture"
    for job, listing in multi_addr_jobs:
        expected_parts = job["address"]
        for part in expected_parts:
            assert part in listing.location_raw, (
                f"Expected {part!r} in location_raw={listing.location_raw!r}"
            )


# ---------------------------------------------------------------------------
# Population-rate acceptance gate
# ---------------------------------------------------------------------------

def test_field_population_rates(parsed_listings):
    """≥90% of listings must have title, employer, location, url populated."""
    n = len(parsed_listings)
    assert n > 0, "Fixture produced no listings"

    def rate(field: str) -> float:
        populated = sum(1 for l in parsed_listings if getattr(l, field))
        return populated / n

    assert rate("title") >= 0.9, f"title population rate {rate('title'):.1%} < 90%"
    assert rate("employer") >= 0.9, f"employer population rate {rate('employer'):.1%} < 90%"
    assert rate("location_raw") >= 0.9, f"location_raw population rate {rate('location_raw'):.1%} < 90%"
    assert rate("url") >= 0.9, f"url population rate {rate('url'):.1%} < 90%"
    assert rate("posted_at") >= 0.9, f"posted_at population rate {rate('posted_at'):.1%} < 90%"
