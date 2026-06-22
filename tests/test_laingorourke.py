"""Unit tests for the Laing O'Rourke Careers adapter — JSON-LD sitemap scrape.

Fixtures:
  tests/fixtures/adapters/laingorourke_sitemap.xml  — 4 /jobs/ URLs + 2 non-job
  tests/fixtures/adapters/laingorourke_job.html     — full JobPosting JSON-LD page

Confirmed JSON-LD structure (live recon 2026-06-22):
  @type: "JobPosting"
  title                   → title
  identifier.value        → source_listing_id (hash)
  datePosted              → posted_at (ISO 8601)
  employmentType          → contract_type_raw
  hiringOrganization.name → employer
  jobLocation[0].address  → location_raw (locality, region, country)
  description             → description_raw (HTML)

All tests use fixtures or mocked HTTP — no live network calls.

Coverage (48 tests):
  _parse_sitemap          : count, filter non-jobs, newest-first sort,
                            no-lastmod sorts last, empty, malformed, lastmod parsed
  _extract_job_jsonld     : fixture HTML, empty, no script, wrong @type, bad JSON
  _build_location_raw     : full address, missing region, list, absent, empty list
  _build_salary_raw       : absent, string, dict min/max, dict single value
  _build_source_listing_id: identifier.value, URL slug fallback
  _parse_date             : ISO datetime Z, ISO date, None, invalid, tz-aware
  _matches_keywords       : title match, description match, no match, empty list
  _jsonld_to_raw_listing  : full mapping, no title, source, employer, contract_type,
                            posted_at, location, salary absent
  LaingORourkeAdapter ctor: name, crawl_delay, max_jobs, keywords, extra kwargs
  fetch()                 : happy path, since filter, keyword filter, dedup by URL,
                            sitemap error, individual page error continues,
                            max_jobs cap, no-date passes since filter
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mechpm.adapters.laingorourke import (
    LaingORourkeAdapter,
    _DEFAULT_CRAWL_DELAY,
    _DEFAULT_MAX_JOBS,
    _EMPLOYER,
    _SOURCE_NAME,
    _build_location_raw,
    _build_salary_raw,
    _build_source_listing_id,
    _extract_job_jsonld,
    _jsonld_to_raw_listing,
    _matches_keywords,
    _parse_date,
    _parse_sitemap,
)

_FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "adapters"
_SITEMAP_FIXTURE = _FIXTURE_DIR / "laingorourke_sitemap.xml"
_JOB_FIXTURE = _FIXTURE_DIR / "laingorourke_job.html"
_BASE_URL = "https://careers.laingorourke.com"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sitemap_xml() -> str:
    return _SITEMAP_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def job_html() -> str:
    return _JOB_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed_sitemap(sitemap_xml) -> list:
    return _parse_sitemap(sitemap_xml, _BASE_URL)


@pytest.fixture(scope="module")
def extracted_jsonld(job_html) -> dict:
    result = _extract_job_jsonld(job_html)
    assert result is not None, "Fixture must contain a JobPosting JSON-LD block"
    return result


@pytest.fixture(scope="module")
def sample_jobposting() -> dict:
    """Minimal fully-populated JobPosting JSON-LD dict for mapping tests."""
    return {
        "@context": "http://schema.org/",
        "@type": "JobPosting",
        "title": "Senior Project Manager",
        "description": "<p>Lead the delivery of major infrastructure projects.</p>",
        "datePosted": "2026-05-27T09:35:48Z",
        "employmentType": "FULL_TIME",
        "validThrough": "2026-09-20T13:25:52Z",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Laing O'Rourke",
        },
        "identifier": {
            "@type": "PropertyValue",
            "name": "Laing O'Rourke",
            "value": "1cd1338423b3ab8e0cbbaf9ddabe4823",
        },
        "jobLocation": [
            {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "streetAddress": "48-50 High Street",
                    "addressLocality": "Leiston",
                    "addressRegion": "",
                    "postalCode": "IP16 4EW",
                    "addressCountry": "GB",
                },
            }
        ],
    }


# ===========================================================================
# 1 – _parse_sitemap
# ===========================================================================


def test_parse_sitemap_returns_correct_count(parsed_sitemap):
    """Fixture has 4 /jobs/ entries."""
    assert len(parsed_sitemap) == 4


def test_parse_sitemap_filters_non_job_urls(parsed_sitemap):
    """Root URL and /why-laing-o-rourke must be excluded."""
    for url, _ in parsed_sitemap:
        assert "/jobs/" in url


def test_parse_sitemap_urls_are_strings(parsed_sitemap):
    for url, _ in parsed_sitemap:
        assert isinstance(url, str)
        assert url.startswith("https://")


def test_parse_sitemap_sorted_newest_first(parsed_sitemap):
    """Entries with lastmod should appear newest-first."""
    dated = [(u, d) for u, d in parsed_sitemap if d is not None]
    dates = [d for _, d in dated]
    assert dates == sorted(dates, reverse=True)


def test_parse_sitemap_no_lastmod_sorts_last(parsed_sitemap):
    """Entry without lastmod (risk-manager-london) must be last."""
    last_url, last_mod = parsed_sitemap[-1]
    assert last_mod is None


def test_parse_sitemap_lastmod_parsed_as_date(parsed_sitemap):
    """First entry should have a date.date lastmod object."""
    _, lastmod = parsed_sitemap[0]
    assert isinstance(lastmod, date)


def test_parse_sitemap_first_entry_is_newest(parsed_sitemap):
    """First entry should be project-quality-manager (lastmod 2026-06-12)."""
    url, lastmod = parsed_sitemap[0]
    assert "project-quality-manager" in url
    assert lastmod == date(2026, 6, 12)


def test_parse_sitemap_empty_xml():
    assert _parse_sitemap("", _BASE_URL) == []


def test_parse_sitemap_whitespace_only():
    assert _parse_sitemap("   \n  ", _BASE_URL) == []


def test_parse_sitemap_malformed_xml():
    assert _parse_sitemap("<not valid xml <<>>", _BASE_URL) == []


def test_parse_sitemap_no_jobs_returns_empty():
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://careers.laingorourke.com/about</loc></url>
    </urlset>"""
    assert _parse_sitemap(xml, _BASE_URL) == []


# ===========================================================================
# 2 – _extract_job_jsonld
# ===========================================================================


def test_extract_job_jsonld_finds_jobposting(job_html):
    result = _extract_job_jsonld(job_html)
    assert result is not None
    assert result.get("@type") == "JobPosting"


def test_extract_job_jsonld_has_title(job_html):
    result = _extract_job_jsonld(job_html)
    assert result["title"] == "Senior Project Manager"


def test_extract_job_jsonld_empty_html():
    assert _extract_job_jsonld("") is None


def test_extract_job_jsonld_no_script_tags():
    html = "<html><body><h1>No scripts here</h1></body></html>"
    assert _extract_job_jsonld(html) is None


def test_extract_job_jsonld_wrong_type():
    html = """<html><body>
    <script type="application/ld+json">{"@type": "Organization", "name": "LOR"}</script>
    </body></html>"""
    assert _extract_job_jsonld(html) is None


def test_extract_job_jsonld_malformed_json():
    html = """<html><body>
    <script type="application/ld+json">{invalid json here}</script>
    </body></html>"""
    assert _extract_job_jsonld(html) is None


def test_extract_job_jsonld_picks_jobposting_from_multiple_scripts():
    html = """<html><body>
    <script type="application/ld+json">{"@type": "BreadcrumbList", "items": []}</script>
    <script type="application/ld+json">{"@type": "JobPosting", "title": "PM"}</script>
    </body></html>"""
    result = _extract_job_jsonld(html)
    assert result is not None
    assert result["title"] == "PM"


# ===========================================================================
# 3 – _build_location_raw
# ===========================================================================


def test_build_location_raw_full_address(sample_jobposting):
    loc = _build_location_raw(sample_jobposting)
    assert "Leiston" in loc
    assert "GB" in loc


def test_build_location_raw_missing_region():
    """Empty addressRegion should be skipped."""
    jsonld = {
        "jobLocation": [
            {
                "@type": "Place",
                "address": {
                    "addressLocality": "Dartford",
                    "addressRegion": "",
                    "addressCountry": "GB",
                },
            }
        ]
    }
    loc = _build_location_raw(jsonld)
    assert loc == "Dartford, GB"


def test_build_location_raw_list_uses_first():
    jsonld = {
        "jobLocation": [
            {"@type": "Place", "address": {"addressLocality": "London", "addressCountry": "GB"}},
            {"@type": "Place", "address": {"addressLocality": "Manchester", "addressCountry": "GB"}},
        ]
    }
    loc = _build_location_raw(jsonld)
    assert "London" in loc


def test_build_location_raw_absent():
    assert _build_location_raw({}) is None


def test_build_location_raw_empty_list():
    assert _build_location_raw({"jobLocation": []}) is None


def test_build_location_raw_single_object():
    """jobLocation may be a dict rather than a list."""
    jsonld = {
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": "Bristol", "addressCountry": "GB"},
        }
    }
    loc = _build_location_raw(jsonld)
    assert "Bristol" in loc


# ===========================================================================
# 4 – _build_salary_raw
# ===========================================================================


def test_build_salary_raw_absent():
    assert _build_salary_raw({}) is None


def test_build_salary_raw_string():
    result = _build_salary_raw({"baseSalary": "£60,000 - £80,000"})
    assert result == "£60,000 - £80,000"


def test_build_salary_raw_dict_min_max():
    jsonld = {
        "baseSalary": {
            "currency": "GBP",
            "value": {
                "minValue": 50000,
                "maxValue": 70000,
                "unitText": "YEAR",
            },
        }
    }
    result = _build_salary_raw(jsonld)
    assert result is not None
    assert "50000" in result
    assert "70000" in result


def test_build_salary_raw_dict_flat_value():
    jsonld = {"baseSalary": {"currency": "GBP", "value": 65000}}
    result = _build_salary_raw(jsonld)
    assert result is not None
    assert "65000" in result


# ===========================================================================
# 5 – _build_source_listing_id
# ===========================================================================


def test_build_source_listing_id_uses_identifier(sample_jobposting):
    url = f"{_BASE_URL}/jobs/senior-project-manager-leiston-united-kingdom"
    sid = _build_source_listing_id(sample_jobposting, url)
    assert sid == "1cd1338423b3ab8e0cbbaf9ddabe4823"


def test_build_source_listing_id_fallback_slug():
    jsonld = {}
    url = f"{_BASE_URL}/jobs/quantity-surveyor-dartford"
    sid = _build_source_listing_id(jsonld, url)
    assert sid == "quantity-surveyor-dartford"


def test_build_source_listing_id_empty_identifier_value_falls_back():
    jsonld = {"identifier": {"@type": "PropertyValue", "value": ""}}
    url = f"{_BASE_URL}/jobs/risk-manager-london"
    sid = _build_source_listing_id(jsonld, url)
    assert sid == "risk-manager-london"


# ===========================================================================
# 6 – _parse_date
# ===========================================================================


def test_parse_date_iso_datetime_z():
    dt = _parse_date("2026-05-27T09:35:48Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 27


def test_parse_date_iso_date_only():
    dt = _parse_date("2026-04-15")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 4


def test_parse_date_returns_none_for_none():
    assert _parse_date(None) is None


def test_parse_date_returns_none_for_invalid():
    assert _parse_date("not-a-date") is None


def test_parse_date_is_utc_aware():
    dt = _parse_date("2026-05-27T09:35:48Z")
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc


def test_parse_date_date_only_is_utc_aware():
    dt = _parse_date("2026-04-15")
    assert dt.tzinfo is not None


# ===========================================================================
# 7 – _matches_keywords
# ===========================================================================


def test_matches_keywords_title_match():
    assert _matches_keywords("Senior Project Manager", None, ["project manager"]) is True


def test_matches_keywords_description_match():
    assert _matches_keywords("Engineer", "<p>We need a project manager</p>", ["project manager"]) is True


def test_matches_keywords_no_match():
    assert _matches_keywords("Software Developer", "Python and Go", ["project manager"]) is False


def test_matches_keywords_empty_list_returns_true():
    assert _matches_keywords("Any Title", "Any description", []) is True


def test_matches_keywords_case_insensitive():
    assert _matches_keywords("QUANTITY SURVEYOR", None, ["quantity surveyor"]) is True


# ===========================================================================
# 8 – _jsonld_to_raw_listing
# ===========================================================================


def test_jsonld_to_raw_listing_full_mapping(sample_jobposting):
    url = f"{_BASE_URL}/jobs/senior-project-manager-leiston-united-kingdom"
    listing = _jsonld_to_raw_listing(sample_jobposting, url)
    assert listing is not None
    assert listing.title == "Senior Project Manager"
    assert listing.source == _SOURCE_NAME
    assert listing.url == url
    assert listing.employer == "Laing O'Rourke"
    assert listing.source_listing_id == "1cd1338423b3ab8e0cbbaf9ddabe4823"
    assert listing.contract_type_raw == "FULL_TIME"
    assert listing.posted_at is not None
    assert listing.location_raw is not None


def test_jsonld_to_raw_listing_no_title():
    jsonld = {"@type": "JobPosting", "description": "Some job"}
    result = _jsonld_to_raw_listing(jsonld, f"{_BASE_URL}/jobs/test")
    assert result is None


def test_jsonld_to_raw_listing_source_is_laingorourke(sample_jobposting):
    url = f"{_BASE_URL}/jobs/test-job"
    listing = _jsonld_to_raw_listing(sample_jobposting, url)
    assert listing.source == _SOURCE_NAME


def test_jsonld_to_raw_listing_employer_from_hiring_org(sample_jobposting):
    url = f"{_BASE_URL}/jobs/test-job"
    listing = _jsonld_to_raw_listing(sample_jobposting, url)
    assert listing.employer == "Laing O'Rourke"


def test_jsonld_to_raw_listing_employer_falls_back_to_constant():
    jsonld = {"@type": "JobPosting", "title": "PM", "hiringOrganization": {}}
    listing = _jsonld_to_raw_listing(jsonld, f"{_BASE_URL}/jobs/pm")
    assert listing.employer == _EMPLOYER


def test_jsonld_to_raw_listing_no_salary():
    jsonld = {"@type": "JobPosting", "title": "Engineer"}
    listing = _jsonld_to_raw_listing(jsonld, f"{_BASE_URL}/jobs/eng")
    assert listing.salary_raw is None


def test_jsonld_to_raw_listing_posted_at(sample_jobposting):
    url = f"{_BASE_URL}/jobs/test-job"
    listing = _jsonld_to_raw_listing(sample_jobposting, url)
    assert listing.posted_at == datetime(2026, 5, 27, 9, 35, 48, tzinfo=timezone.utc)


def test_jsonld_to_raw_listing_valid_through_in_metadata(sample_jobposting):
    url = f"{_BASE_URL}/jobs/test-job"
    listing = _jsonld_to_raw_listing(sample_jobposting, url)
    assert listing.metadata.get("validThrough") == "2026-09-20T13:25:52Z"


# ===========================================================================
# 9 – Adapter constructor
# ===========================================================================


def test_adapter_name():
    adapter = LaingORourkeAdapter()
    assert adapter.name == _SOURCE_NAME


def test_adapter_crawl_delay_default():
    adapter = LaingORourkeAdapter()
    assert adapter.crawl_delay == _DEFAULT_CRAWL_DELAY


def test_adapter_max_jobs_default():
    adapter = LaingORourkeAdapter()
    assert adapter.max_jobs == _DEFAULT_MAX_JOBS


def test_adapter_accepts_custom_keywords():
    adapter = LaingORourkeAdapter(keywords_list=["project manager"])
    assert "project manager" in adapter.keywords_list


def test_adapter_extra_kwargs_ignored():
    adapter = LaingORourkeAdapter(unknown_kwarg="ignored")
    assert adapter.name == _SOURCE_NAME


def test_adapter_base_url_stripped():
    adapter = LaingORourkeAdapter(base_url="https://careers.laingorourke.com/")
    assert not adapter.base_url.endswith("/")


# ===========================================================================
# 10 – fetch() mock tests
# ===========================================================================


def _make_mock_response(content: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=resp
        )
    return resp


def _make_mock_client(responses: list[MagicMock]) -> AsyncMock:
    call_index = [0]

    async def _get(url, **kwargs):
        idx = call_index[0]
        call_index[0] += 1
        if idx < len(responses):
            return responses[idx]
        return _make_mock_response("<html><body></body></html>")

    mock = AsyncMock()
    mock.get = _get
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


def test_fetch_happy_path(sitemap_xml, job_html):
    """Sitemap → 4 job URLs; all return the PM job HTML → 4 unique listings (different URLs)."""
    adapter = LaingORourkeAdapter(
        keywords_list=["project manager"],
        max_jobs=4,
        crawl_delay=0,
    )
    sitemap_resp = _make_mock_response(sitemap_xml)
    # 4 job page responses (sitemap has 4 /jobs/ entries)
    job_responses = [_make_mock_response(job_html) for _ in range(4)]
    mock_client = _make_mock_client([sitemap_resp] + job_responses)

    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    # 4 jobs fetched; each has a different URL from the sitemap → 4 unique listings
    assert len(listings) == 4
    assert all(l.source == _SOURCE_NAME for l in listings)


def test_fetch_since_filter_excludes_old_listings(sitemap_xml, job_html):
    """Listings older than since are dropped."""
    adapter = LaingORourkeAdapter(keywords_list=[], max_jobs=1, crawl_delay=0)
    sitemap_resp = _make_mock_response(sitemap_xml)
    job_resp = _make_mock_response(job_html)
    mock_client = _make_mock_client([sitemap_resp, job_resp])

    # since = far future → all listings are "old"
    since = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch(since=since))

    assert listings == []


def test_fetch_since_filter_keeps_recent_listings(sitemap_xml, job_html):
    """Listings newer than since are kept."""
    adapter = LaingORourkeAdapter(keywords_list=[], max_jobs=1, crawl_delay=0)
    sitemap_resp = _make_mock_response(sitemap_xml)
    job_resp = _make_mock_response(job_html)
    mock_client = _make_mock_client([sitemap_resp, job_resp])

    # since = well before the fixture's datePosted (2026-05-27)
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch(since=since))

    assert len(listings) == 1


def test_fetch_keyword_filter_excludes_non_matching(sitemap_xml, job_html):
    """Listings not matching keywords are excluded."""
    adapter = LaingORourkeAdapter(
        keywords_list=["data scientist"],
        max_jobs=1,
        crawl_delay=0,
    )
    sitemap_resp = _make_mock_response(sitemap_xml)
    job_resp = _make_mock_response(job_html)
    mock_client = _make_mock_client([sitemap_resp, job_resp])

    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_sitemap_http_error_returns_empty():
    """HTTP error fetching sitemap → returns []."""
    adapter = LaingORourkeAdapter(keywords_list=[], max_jobs=10, crawl_delay=0)
    sitemap_resp = _make_mock_response("", status_code=503)
    mock_client = _make_mock_client([sitemap_resp])

    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_individual_page_error_continues(sitemap_xml, job_html):
    """A 404 on one job page should not stop the rest of the run."""
    adapter = LaingORourkeAdapter(keywords_list=[], max_jobs=3, crawl_delay=0)
    sitemap_resp = _make_mock_response(sitemap_xml)
    error_resp = _make_mock_response("", status_code=404)
    ok_resp = _make_mock_response(job_html)
    # sitemap OK, job1 → 404, job2 → OK, job3 → OK
    mock_client = _make_mock_client([sitemap_resp, error_resp, ok_resp, ok_resp])

    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    # job1 fails (404), job2 and job3 both succeed with different URLs → 2 unique
    assert len(listings) == 2


def test_fetch_max_jobs_cap(sitemap_xml, job_html):
    """max_jobs=1 should only fetch one job page after the sitemap."""
    adapter = LaingORourkeAdapter(keywords_list=[], max_jobs=1, crawl_delay=0)
    sitemap_resp = _make_mock_response(sitemap_xml)
    job_resp = _make_mock_response(job_html)
    mock_client = _make_mock_client([sitemap_resp, job_resp])

    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 1


def test_fetch_no_date_passes_since_filter(sitemap_xml):
    """Jobs with posted_at=None must not be filtered out by the since filter."""
    no_date_html = """<html><body>
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "Project Manager", "description": "PM role"}
    </script></body></html>"""

    adapter = LaingORourkeAdapter(keywords_list=[], max_jobs=1, crawl_delay=0)
    sitemap_resp = _make_mock_response(sitemap_xml)
    job_resp = _make_mock_response(no_date_html)
    mock_client = _make_mock_client([sitemap_resp, job_resp])

    since = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with patch("mechpm.adapters.laingorourke.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.laingorourke.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch(since=since))

    assert len(listings) == 1
