"""Fixture-driven unit tests for the Aviation Job Search adapter parser.

Loads saved schema.org/JobPosting LD+JSON blobs from
``tests/fixtures/adapters/aviation_job_search_page1.json`` and asserts that
``_map_ldjson_job()`` populates the expected fields.

Field mapping confirmed 2026-06-15 against live Aviation Job Search pages:

    hiringOrganization.name       → employer
    jobLocation.address.*         → location_raw (locality, region, country)
    datePosted                    → posted_at  (ISO date, e.g. "2026-05-26")
    url                           → url  (absolute, canonical)
    employmentType                → contract_type_raw  (e.g. "FULL_TIME")
    title                         → title
    Trailing -NNN from URL slug   → source_listing_id
    metadata["aggregator"]        → True (aggregator board)
"""
from __future__ import annotations

import json
import pathlib
from datetime import timezone

import pytest

from mechpm.adapters.aviation_job_search import _map_ldjson_job

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "adapters"
    / "aviation_job_search_page1.json"
)

_BASE_URL = "https://www.aviationjobsearch.com"


@pytest.fixture(scope="module")
def fixture_jobs() -> list[dict]:
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["jobs"]


@pytest.fixture(scope="module")
def parsed_listings(fixture_jobs):
    return [_map_ldjson_job(job, job["url"]) for job in fixture_jobs]


# ---------------------------------------------------------------------------
# Per-field population tests
# ---------------------------------------------------------------------------

def test_source_is_aviation_job_search(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == "aviation_job_search"


def test_source_listing_id_is_numeric_str(parsed_listings):
    """source_listing_id must be the numeric job ID extracted from the URL slug."""
    for listing in parsed_listings:
        assert listing is not None
        assert listing.source_listing_id.isdigit(), (
            f"source_listing_id expected numeric digits, got {listing.source_listing_id!r}"
        )


def test_title_populated(fixture_jobs, parsed_listings):
    for job, listing in zip(fixture_jobs, parsed_listings):
        assert listing.title == job["title"], (
            f"title mismatch: expected {job['title']!r}, got {listing.title!r}"
        )


def test_employer_from_hiring_organization(fixture_jobs, parsed_listings):
    """employer must come from hiringOrganization.name."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        expected = (job.get("hiringOrganization") or {}).get("name")
        assert listing.employer == expected, (
            f"employer mismatch: expected {expected!r}, got {listing.employer!r}"
        )


def test_location_raw_contains_locality(fixture_jobs, parsed_listings):
    """location_raw must include the addressLocality value when present."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        address = (job.get("jobLocation") or {}).get("address") or {}
        locality = address.get("addressLocality")
        if locality:
            assert locality in (listing.location_raw or ""), (
                f"expected locality {locality!r} in location_raw={listing.location_raw!r}"
            )


def test_url_matches_job_url(fixture_jobs, parsed_listings):
    for job, listing in zip(fixture_jobs, parsed_listings):
        assert listing.url == job["url"], (
            f"url mismatch: expected {job['url']!r}, got {listing.url!r}"
        )


def test_posted_at_parsed_from_date_posted(fixture_jobs, parsed_listings):
    """posted_at must be a UTC-aware datetime when datePosted is present."""
    for job, listing in zip(fixture_jobs, parsed_listings):
        if job.get("datePosted"):
            assert listing.posted_at is not None, (
                f"posted_at is None for job with datePosted={job['datePosted']!r}"
            )
            assert listing.posted_at.tzinfo is not None, (
                "posted_at must be timezone-aware"
            )
            assert listing.posted_at.tzinfo == timezone.utc


def test_contract_type_raw_populated(parsed_listings):
    for listing in parsed_listings:
        assert listing.contract_type_raw is not None


def test_aggregator_metadata_set(parsed_listings):
    """All listings must carry metadata['aggregator'] = True."""
    for listing in parsed_listings:
        assert listing.metadata.get("aggregator") is True, (
            f"aggregator flag missing in metadata for listing {listing.source_listing_id}"
        )


# ---------------------------------------------------------------------------
# Spot-check: job[0] — Engineering Project Manager at JMC Aviation, Exeter
# ---------------------------------------------------------------------------

def test_first_job_full_field_map(fixture_jobs, parsed_listings):
    """Spot-check job[0]: Engineering Project Manager, JMC Aviation, Exeter."""
    listing = parsed_listings[0]

    assert listing.source_listing_id == "607645"
    assert listing.title == "Engineering Project Manager"
    assert listing.employer == "JMC Aviation"
    assert "Exeter" in listing.location_raw
    assert listing.url == fixture_jobs[0]["url"]
    assert listing.posted_at is not None
    assert listing.posted_at.year == 2026
    assert listing.posted_at.month == 5
    assert listing.posted_at.day == 26
    assert listing.contract_type_raw == "FULL_TIME"
    assert listing.metadata.get("valid_through") == "2026-06-22"


def test_second_job_location_multi_part(fixture_jobs, parsed_listings):
    """Spot-check job[1]: Part-145 Maintenance Manager — multi-part UK location."""
    listing = parsed_listings[1]

    assert listing.source_listing_id == "615688"
    assert listing.title == "Part-145 Maintenance Manager"
    assert listing.employer == "Volantes Technical Recruitment Ltd"
    assert "Saint Athan" in listing.location_raw
    assert "Wales" in listing.location_raw
    assert listing.posted_at is not None
    assert listing.posted_at.year == 2026
    assert listing.posted_at.month == 6


# ---------------------------------------------------------------------------
# Population-rate acceptance gate
# ---------------------------------------------------------------------------

def test_field_population_rates(parsed_listings):
    """≥80% of fixture listings must have title, employer, location_raw, url, posted_at."""
    n = len(parsed_listings)
    assert n > 0, "Fixture produced no listings"

    def rate(field: str) -> float:
        populated = sum(1 for lst in parsed_listings if getattr(lst, field))
        return populated / n

    assert rate("title") >= 0.8, f"title population rate {rate('title'):.1%} < 80%"
    assert rate("employer") >= 0.8, f"employer population rate {rate('employer'):.1%} < 80%"
    assert rate("location_raw") >= 0.8, f"location_raw {rate('location_raw'):.1%} < 80%"
    assert rate("url") >= 0.8, f"url {rate('url'):.1%} < 80%"
    assert rate("posted_at") >= 0.8, f"posted_at {rate('posted_at'):.1%} < 80%"
