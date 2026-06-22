"""Unit tests for the Volcanic platform adapter (Navartis + Carrington West).

Covers both Navartis (JSON-LD + aside[data-element="job-details"]) and
Carrington West (dd.flex-item HTML layout) parsing, plus the shared
VolcanicAdapter class.

All tests use synthetic data or HTML fixtures — no live network calls.

Coverage (50 tests):
  _extract_id_from_url  : navartis slug, cw slug, no number → slug, empty,
                          slash-only path, url-only-digits
  _parse_sitemap_xml    : valid with lastmod sorted desc, no lastmod,
                          empty, malformed XML, multiple URLs sorted,
                          non-job URLs filtered
  _parse_date_text      : "19 June 2026", "1 January 2025", "30 December 2024",
                          unknown format, empty string, None, text with prefix
  _parse_iso_datetime   : full ISO-Z, date-only, offset, bad string, None
  _matches_keywords     : match in title, match in desc, no match,
                          empty keywords bypass
  _extract_navartis_fields (via fixture HTML):
                          title from JSON-LD, title fallback h1,
                          posted_at from JSON-LD datePosted,
                          location from HTML aside (overrides JSON-LD),
                          salary from HTML aside,
                          contract_type from HTML aside,
                          source_listing_id from HTML aside (job ref),
                          description_raw from JSON-LD
  _extract_cw_fields (via fixture HTML):
                          title from h1.job-title,
                          posted_at parsed "20 June 2026",
                          salary from span.salary-free,
                          location stripped of label,
                          contract_type from anchor,
                          source_listing_id from dd.job-ref,
                          description_raw from div.job-description
  _parse_job_html       : navartis layout detected, cw layout detected,
                          empty html returns None, no title returns None,
                          source_name in RawListing.source,
                          employer_name in RawListing.employer,
                          source_listing_id from URL when HTML has none
  VolcanicAdapter ctor  : name=source_name, base_url stripped, default max_jobs,
                          keywords_list stored, extra kwargs ignored
  fetch()               : happy path returns listings, keyword filter,
                          since filter drops old, since keeps no-date,
                          dedup by url, sitemap HTTP error → [],
                          sitemap request error → [], job HTTP error skips,
                          navartis source in listings, carrington_west source
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from selectolax.parser import HTMLParser

from mechpm.adapters.volcanic import (
    VolcanicAdapter,
    _extract_cw_fields,
    _extract_id_from_url,
    _extract_navartis_fields,
    _matches_keywords,
    _parse_date_text,
    _parse_iso_datetime,
    _parse_job_html,
    _parse_sitemap_xml,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "adapters"
_NAVARTIS_HTML = (_FIXTURES / "navartis_job.html").read_text(encoding="utf-8")
_CW_HTML = (_FIXTURES / "cw_job.html").read_text(encoding="utf-8")

_NAVARTIS_BASE = "https://www.navartisglobal.com"
_CW_BASE = "https://www.carringtonwest.com"

# ---------------------------------------------------------------------------
# Sitemap XML builder helpers
# ---------------------------------------------------------------------------

_SITEMAP_OPEN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
)
_SITEMAP_CLOSE = "</urlset>"


def _sitemap_xml(entries: list[tuple[str, str | None]]) -> str:
    """Build minimal Volcanic sitemap XML from (url, lastmod) pairs."""
    items = []
    for loc, lastmod in entries:
        el = f"<url><loc>{loc}</loc>"
        if lastmod:
            el += f"<lastmod>{lastmod}</lastmod>"
        el += "</url>"
        items.append(el)
    return _SITEMAP_OPEN + "".join(items) + _SITEMAP_CLOSE


def _make_mock_response(body: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    return resp


def _mock_client(responses: list[MagicMock]) -> AsyncMock:
    """Return an AsyncMock httpx.AsyncClient that serves responses in order."""
    call_idx = [0]

    async def _get(url, **kwargs):
        i = call_idx[0]
        call_idx[0] += 1
        if i < len(responses):
            return responses[i]
        return _make_mock_response("", status_code=404)

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=_get)
    return mock


# ---------------------------------------------------------------------------
# Minimal job HTML factories
# ---------------------------------------------------------------------------

_NAVARTIS_URL = f"{_NAVARTIS_BASE}/job/project-manager-rail-200001"
_CW_URL = f"{_CW_BASE}/job/project-manager-highways-99999"


def _navartis_html(
    title: str = "Project Manager",
    date_posted: str = "2026-06-20T09:00:00.000Z",
    location: str = "Birmingham",
    salary: str = "£500 - £600 per day",
    contract: str = "Contract",
    job_ref: str = "PR/200001",
    description: str = "<p>Project Manager contract role in rail.</p>",
) -> str:
    """Build a minimal Navartis-style job page HTML fragment."""
    ld = json.dumps(
        {
            "@context": "http://schema.org/",
            "@type": "JobPosting",
            "title": title,
            "description": description,
            "identifier": {"@type": "PropertyValue", "name": "Navartis", "value": job_ref},
            "datePosted": date_posted,
            "employmentType": [contract],
            "hiringOrganization": {"@type": "Organization", "name": "Navartis"},
            "baseSalary": None,
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": location,
                    "addressRegion": "England",
                    "addressCountry": "GB",
                },
            },
        }
    )
    return f"""<!DOCTYPE html>
<html><head>
<script type="application/ld+json">{ld}</script>
</head><body>
<h1 class="h2 m-0">{title}</h1>
<aside data-element="job-details">
  <ul>
    <li><div class="row">
      <strong class="font-weight-bold">Location:</strong>
      <span class="col">{location}</span>
    </div></li>
    <li><div class="row">
      <strong class="font-weight-bold">Salary:</strong>
      <span class="col">{salary}</span>
    </div></li>
    <li><div class="row">
      <strong class="font-weight-bold">Employment Type:</strong>
      <span class="col">{contract}</span>
    </div></li>
    <li><div class="row">
      <strong class="font-weight-bold">Job Ref:</strong>
      <span class="col">{job_ref}</span>
    </div></li>
  </ul>
</aside>
</body></html>"""


def _cw_html(
    title: str = "Project Manager - Highways",
    date_text: str = "20 June 2026",
    salary: str = "£500 - £600 per day",
    location: str = "Manchester",
    job_type: str = "Contract - Full Time",
    job_ref: str = "JO0000099999",
    description: str = "<p>Project Manager contract role in highways.</p>",
) -> str:
    """Build a minimal Carrington West-style job page HTML fragment."""
    return f"""<!DOCTYPE html>
<html><head></head><body>
<div id="job-page" class="job grid">
  <div class="main-content clearfix">
    <header>
      <h1 class="job-title h1">{title}</h1>
    </header>
    <div class="job-details flex-group">
      <dl class="flex-container">
        <dd class="flex-item date-posted">
          <span class="date-posted">Posted</span>{date_text}
        </dd>
        <dd class="flex-item job-salary">
          <span>Salary</span>
          <span class="salary-free">{salary}</span>
        </dd>
        <dd class="flex-item job-location">
          <span>Location</span>{location}</dd>
        <dd class="flex-item job-type">
          <span>Job type</span>
          <a href="/jobs/contract">{job_type}</a>
        </dd>
        <dd class="flex-item job-ref">
          <span>Reference</span>{job_ref}</dd>
      </dl>
    </div>
    <article class="desc">
      <div class="job-description js-job-description">{description}</div>
    </article>
  </div>
</div>
</body></html>"""


# ===========================================================================
# _extract_id_from_url
# ===========================================================================


class TestExtractIdFromUrl:
    def test_navartis_trailing_number(self):
        url = f"{_NAVARTIS_BASE}/job/commercial-assistant-pr-slash-172416"
        assert _extract_id_from_url(url) == "172416"

    def test_cw_trailing_number(self):
        url = f"{_CW_BASE}/job/highways-officer-5941945"
        assert _extract_id_from_url(url) == "5941945"

    def test_no_trailing_number_returns_full_slug(self):
        url = f"{_CW_BASE}/job/project-manager"
        assert _extract_id_from_url(url) == "project-manager"

    def test_empty_url_returns_empty_string(self):
        assert _extract_id_from_url("") == ""

    def test_url_with_only_slash_returns_domain_slug(self):
        # A URL with only a trailing slash yields the domain as the slug (no /job/ path)
        result = _extract_id_from_url("https://example.com/")
        assert result == "example.com"

    def test_long_slug_extracts_last_number(self):
        url = f"{_NAVARTIS_BASE}/job/senior-project-manager-rail-contract-567890"
        assert _extract_id_from_url(url) == "567890"


# ===========================================================================
# _parse_sitemap_xml
# ===========================================================================


class TestParseSitemapXml:
    def test_valid_sitemap_returns_entries(self):
        xml = _sitemap_xml([
            (f"{_NAVARTIS_BASE}/job/job-a-100", "2026-06-20"),
        ])
        entries = _parse_sitemap_xml(xml)
        assert len(entries) == 1
        assert entries[0][0] == f"{_NAVARTIS_BASE}/job/job-a-100"

    def test_entries_sorted_by_lastmod_descending(self):
        xml = _sitemap_xml([
            (f"{_NAVARTIS_BASE}/job/old-100", "2026-01-01"),
            (f"{_NAVARTIS_BASE}/job/new-200", "2026-06-20"),
            (f"{_NAVARTIS_BASE}/job/mid-300", "2026-03-15"),
        ])
        entries = _parse_sitemap_xml(xml)
        dates = [e[1] for e in entries]
        assert dates == sorted(dates, reverse=True)
        assert entries[0][0].endswith("new-200")

    def test_no_lastmod_entry_included_and_sorts_last(self):
        xml = _sitemap_xml([
            (f"{_NAVARTIS_BASE}/job/dated-100", "2026-06-20"),
            (f"{_NAVARTIS_BASE}/job/undated-200", None),
        ])
        entries = _parse_sitemap_xml(xml)
        assert len(entries) == 2
        assert entries[0][0].endswith("dated-100")
        assert entries[1][1] is None

    def test_empty_xml_returns_empty_list(self):
        assert _parse_sitemap_xml("") == []

    def test_malformed_xml_returns_empty_list(self):
        assert _parse_sitemap_xml("<bad><xml>not closed") == []

    def test_non_job_urls_filtered_out(self):
        xml = _sitemap_xml([
            (f"{_NAVARTIS_BASE}/jobs/", "2026-06-20"),
            (f"{_NAVARTIS_BASE}/job/some-role-123", "2026-06-20"),
            (f"{_NAVARTIS_BASE}/about", "2026-06-20"),
        ])
        entries = _parse_sitemap_xml(xml)
        assert len(entries) == 1
        assert "/job/" in entries[0][0]

    def test_lastmod_parsed_as_date_object(self):
        xml = _sitemap_xml([
            (f"{_NAVARTIS_BASE}/job/role-100", "2026-06-20"),
        ])
        entries = _parse_sitemap_xml(xml)
        assert isinstance(entries[0][1], date)
        assert entries[0][1] == date(2026, 6, 20)


# ===========================================================================
# _parse_date_text
# ===========================================================================


class TestParseDateText:
    def test_standard_date(self):
        dt = _parse_date_text("19 June 2026")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 19
        assert dt.tzinfo == timezone.utc

    def test_single_digit_day(self):
        dt = _parse_date_text("1 January 2025")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 1

    def test_december_date(self):
        dt = _parse_date_text("30 December 2024")
        assert dt is not None
        assert dt.month == 12
        assert dt.day == 30

    def test_unknown_format_returns_none(self):
        assert _parse_date_text("not a date") is None

    def test_empty_string_returns_none(self):
        assert _parse_date_text("") is None

    def test_none_returns_none(self):
        assert _parse_date_text(None) is None

    def test_date_with_label_prefix(self):
        # CW HTML includes "Posted" prefix in the text
        dt = _parse_date_text("Posted20 June 2026")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 20


# ===========================================================================
# _parse_iso_datetime
# ===========================================================================


class TestParseIsoDatetime:
    def test_full_iso_z_suffix(self):
        dt = _parse_iso_datetime("2026-06-20T09:00:00.000Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 20
        assert dt.tzinfo == timezone.utc

    def test_date_only(self):
        dt = _parse_iso_datetime("2026-06-20")
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo == timezone.utc

    def test_offset_converted_to_utc(self):
        dt = _parse_iso_datetime("2026-06-20T10:00:00+01:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 9  # +01:00 → UTC 09:00

    def test_bad_string_returns_none(self):
        assert _parse_iso_datetime("not-a-date") is None

    def test_none_returns_none(self):
        assert _parse_iso_datetime(None) is None


# ===========================================================================
# _matches_keywords
# ===========================================================================


class TestMatchesKeywords:
    def test_match_in_title(self):
        assert _matches_keywords(
            "Senior Project Manager", None, ["project manager"]
        )

    def test_match_in_description(self):
        assert _matches_keywords(
            "Site Supervisor",
            "We need a project manager for this contract role.",
            ["project manager"],
        )

    def test_no_match_returns_false(self):
        assert not _matches_keywords(
            "Software Developer",
            "Python Django REST API",
            ["project manager", "quantity surveyor"],
        )

    def test_empty_keywords_always_true(self):
        assert _matches_keywords("Any Title", "Any description", [])

    def test_case_insensitive_match(self):
        assert _matches_keywords(
            "PROGRAMME MANAGER", None, ["programme manager"]
        )


# ===========================================================================
# _extract_navartis_fields  (via fixture + builder)
# ===========================================================================


class TestExtractNavartisFields:
    """Tests for Navartis JSON-LD + HTML aside extraction."""

    def _tree(self, html: str) -> HTMLParser:
        return HTMLParser(html)

    def test_title_from_json_ld(self):
        fields = _extract_navartis_fields(self._tree(_navartis_html(title="Risk Manager")))
        assert fields["title"] == "Risk Manager"

    def test_title_fallback_to_h1_when_no_json_ld(self):
        html = '<html><body><h1 class="h2 m-0">Quantity Surveyor</h1><aside data-element="job-details"><ul></ul></aside></body></html>'
        fields = _extract_navartis_fields(self._tree(html))
        assert fields["title"] == "Quantity Surveyor"

    def test_posted_at_from_json_ld(self):
        fields = _extract_navartis_fields(
            self._tree(_navartis_html(date_posted="2026-06-20T09:00:00.000Z"))
        )
        assert fields["posted_at"] is not None
        assert fields["posted_at"].year == 2026
        assert fields["posted_at"].day == 20

    def test_location_from_html_aside(self):
        fields = _extract_navartis_fields(
            self._tree(_navartis_html(location="Edinburgh"))
        )
        assert fields["location_raw"] == "Edinburgh"

    def test_salary_from_html_aside(self):
        fields = _extract_navartis_fields(
            self._tree(_navartis_html(salary="£600 - £700 per day"))
        )
        assert fields["salary_raw"] == "£600 - £700 per day"

    def test_contract_type_from_html_aside(self):
        fields = _extract_navartis_fields(
            self._tree(_navartis_html(contract="Contract"))
        )
        assert fields["contract_type_raw"] == "Contract"

    def test_source_listing_id_from_job_ref(self):
        fields = _extract_navartis_fields(
            self._tree(_navartis_html(job_ref="PR/999888"))
        )
        assert fields["source_listing_id"] == "PR/999888"

    def test_description_raw_from_json_ld(self):
        desc = "<p>This is the job description for a Project Manager.</p>"
        fields = _extract_navartis_fields(self._tree(_navartis_html(description=desc)))
        assert fields["description_raw"] is not None
        assert "Project Manager" in fields["description_raw"]

    def test_full_fixture_html(self):
        """Verify all key fields extracted from navartis_job.html fixture."""
        tree = HTMLParser(_NAVARTIS_HTML)
        fields = _extract_navartis_fields(tree)
        assert fields["title"] == "Project Manager - Rail"
        assert fields["location_raw"] == "Birmingham"
        assert fields["salary_raw"] == "£450 - £550 per day"
        assert fields["contract_type_raw"] == "Contract"
        assert fields["source_listing_id"] == "PR/200001"
        assert fields["posted_at"] is not None
        assert fields["description_raw"] is not None


# ===========================================================================
# _extract_cw_fields  (via builder + fixture)
# ===========================================================================


class TestExtractCwFields:
    """Tests for Carrington West dd.flex-item HTML extraction."""

    def _tree(self, html: str) -> HTMLParser:
        return HTMLParser(html)

    def test_title_from_h1_job_title(self):
        fields = _extract_cw_fields(self._tree(_cw_html(title="Commercial Manager")))
        assert fields["title"] == "Commercial Manager"

    def test_posted_at_from_date_text(self):
        fields = _extract_cw_fields(self._tree(_cw_html(date_text="20 June 2026")))
        assert fields["posted_at"] is not None
        assert fields["posted_at"].year == 2026
        assert fields["posted_at"].month == 6
        assert fields["posted_at"].day == 20

    def test_salary_from_salary_free_span(self):
        fields = _extract_cw_fields(self._tree(_cw_html(salary="£600 per day")))
        assert fields["salary_raw"] == "£600 per day"

    def test_location_stripped_of_label(self):
        fields = _extract_cw_fields(self._tree(_cw_html(location="Leeds")))
        assert fields["location_raw"] == "Leeds"

    def test_contract_type_from_anchor(self):
        fields = _extract_cw_fields(self._tree(_cw_html(job_type="Contract - Part Time")))
        assert fields["contract_type_raw"] == "Contract - Part Time"

    def test_source_listing_id_from_job_ref(self):
        fields = _extract_cw_fields(self._tree(_cw_html(job_ref="JO0000012345")))
        assert fields["source_listing_id"] == "JO0000012345"

    def test_description_raw_from_job_description_div(self):
        desc = "<p>Project Manager contract role in highways sector.</p>"
        fields = _extract_cw_fields(self._tree(_cw_html(description=desc)))
        assert fields["description_raw"] is not None
        assert "highways" in fields["description_raw"].lower()

    def test_full_fixture_html(self):
        """Verify all key fields extracted from cw_job.html fixture."""
        tree = HTMLParser(_CW_HTML)
        fields = _extract_cw_fields(tree)
        assert fields["title"] == "Project Manager - Highways"
        assert fields["location_raw"] == "Manchester"
        assert fields["salary_raw"] == "£500 - £600 per day"
        assert fields["contract_type_raw"] == "Contract - Full Time"
        assert fields["source_listing_id"] == "JO0000099999"
        assert fields["posted_at"] is not None
        assert fields["posted_at"].day == 20
        assert fields["description_raw"] is not None


# ===========================================================================
# _parse_job_html
# ===========================================================================


class TestParseJobHtml:
    def test_navartis_layout_detected_and_parsed(self):
        listing = _parse_job_html(
            _NAVARTIS_HTML, _NAVARTIS_URL, "navartis", "Navartis"
        )
        assert listing is not None
        assert listing.title == "Project Manager - Rail"

    def test_cw_layout_detected_and_parsed(self):
        listing = _parse_job_html(_CW_HTML, _CW_URL, "carrington_west", "Carrington West")
        assert listing is not None
        assert listing.title == "Project Manager - Highways"

    def test_empty_html_returns_none(self):
        assert _parse_job_html("", _NAVARTIS_URL, "navartis", "Navartis") is None

    def test_whitespace_html_returns_none(self):
        assert _parse_job_html("   ", _NAVARTIS_URL, "navartis", "Navartis") is None

    def test_html_without_title_returns_none(self):
        html = "<html><body><p>No title here</p></body></html>"
        assert _parse_job_html(html, _NAVARTIS_URL, "navartis", "Navartis") is None

    def test_source_name_in_raw_listing(self):
        listing = _parse_job_html(
            _NAVARTIS_HTML, _NAVARTIS_URL, "navartis", "Navartis"
        )
        assert listing is not None
        assert listing.source == "navartis"

    def test_employer_name_in_raw_listing(self):
        listing = _parse_job_html(
            _CW_HTML, _CW_URL, "carrington_west", "Carrington West"
        )
        assert listing is not None
        assert listing.employer == "Carrington West"

    def test_source_listing_id_from_url_when_html_has_none(self):
        # HTML has no details aside and no ref — should fall back to URL id
        minimal_navartis = (
            '<html><body>'
            '<h1 class="h2 m-0">Test Role</h1>'
            '<aside data-element="job-details"><ul></ul></aside>'
            '</body></html>'
        )
        url = f"{_NAVARTIS_BASE}/job/test-role-999111"
        listing = _parse_job_html(minimal_navartis, url, "navartis", "Navartis")
        assert listing is not None
        assert listing.source_listing_id == "999111"

    def test_navartis_salary_from_html_aside(self):
        html = _navartis_html(salary="£550 - £650 per day")
        listing = _parse_job_html(html, _NAVARTIS_URL, "navartis", "Navartis")
        assert listing is not None
        assert listing.salary_raw == "£550 - £650 per day"

    def test_cw_posted_at_parsed(self):
        html = _cw_html(date_text="15 May 2026")
        listing = _parse_job_html(html, _CW_URL, "carrington_west", "Carrington West")
        assert listing is not None
        assert listing.posted_at is not None
        assert listing.posted_at.month == 5
        assert listing.posted_at.day == 15


# ===========================================================================
# VolcanicAdapter constructor
# ===========================================================================


class TestVolcanicAdapterConstructor:
    def test_name_is_source_name(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE, source_name="navartis"
        )
        assert adapter.name == "navartis"

    def test_base_url_trailing_slash_stripped(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE + "/", source_name="navartis"
        )
        assert adapter.base_url == _NAVARTIS_BASE

    def test_default_max_jobs(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE, source_name="navartis"
        )
        assert adapter.max_jobs == 200

    def test_keywords_list_stored(self):
        kws = ["project manager", "risk manager"]
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE, source_name="navartis", keywords_list=kws
        )
        assert adapter.keywords_list == kws

    def test_none_keywords_becomes_empty_list(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE, source_name="navartis", keywords_list=None
        )
        assert adapter.keywords_list == []

    def test_extra_kwargs_ignored(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE,
            source_name="navartis",
            unknown_param="ignored",
        )
        assert adapter.name == "navartis"

    def test_crawl_delay_stored(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE, source_name="navartis", crawl_delay=5
        )
        assert adapter.crawl_delay == 5

    def test_employer_name_derived_from_source_name(self):
        adapter = VolcanicAdapter(
            base_url=_CW_BASE, source_name="carrington_west"
        )
        assert adapter.employer_name == "Carrington West"

    def test_navartis_employer_name(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE, source_name="navartis"
        )
        assert adapter.employer_name == "Navartis"

    def test_sitemap_url_built_correctly(self):
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE, source_name="navartis"
        )
        assert adapter._sitemap_url() == f"{_NAVARTIS_BASE}/job/sitemap.xml"


# ===========================================================================
# VolcanicAdapter.fetch() — mocked HTTP
# ===========================================================================


def _make_navartis_adapter(
    keywords: list[str] | None = None,
    max_jobs: int = 10,
) -> VolcanicAdapter:
    return VolcanicAdapter(
        base_url=_NAVARTIS_BASE,
        source_name="navartis",
        crawl_delay=0,
        keywords_list=keywords or ["project manager"],
        max_jobs=max_jobs,
    )


def _make_cw_adapter(
    keywords: list[str] | None = None,
    max_jobs: int = 10,
) -> VolcanicAdapter:
    return VolcanicAdapter(
        base_url=_CW_BASE,
        source_name="carrington_west",
        crawl_delay=0,
        keywords_list=keywords or ["project manager"],
        max_jobs=max_jobs,
    )


class TestFetch:
    def test_happy_path_navartis_returns_listings(self):
        sitemap = _sitemap_xml([(f"{_NAVARTIS_BASE}/job/project-manager-200001", "2026-06-20")])
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(_NAVARTIS_HTML),
        ])
        adapter = _make_navartis_adapter()
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 1
        assert listings[0].source == "navartis"

    def test_happy_path_cw_returns_listings(self):
        sitemap = _sitemap_xml([(f"{_CW_BASE}/job/project-manager-99999", "2026-06-20")])
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(_CW_HTML),
        ])
        adapter = _make_cw_adapter()
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 1
        assert listings[0].source == "carrington_west"

    def test_keyword_filter_drops_non_matching(self):
        sitemap = _sitemap_xml([(f"{_NAVARTIS_BASE}/job/software-dev-300001", "2026-06-20")])
        # HTML with non-matching title and description
        non_match_html = _navartis_html(
            title="Software Developer",
            description="<p>Python Django REST API development role.</p>",
        )
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(non_match_html),
        ])
        adapter = _make_navartis_adapter(keywords=["project manager"])
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert listings == []

    def test_keyword_filter_keeps_matching(self):
        sitemap = _sitemap_xml([(f"{_NAVARTIS_BASE}/job/project-manager-200001", "2026-06-20")])
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(_NAVARTIS_HTML),  # title = "Project Manager - Rail"
        ])
        adapter = _make_navartis_adapter(keywords=["project manager"])
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 1

    def test_since_filter_drops_old_listings(self):
        sitemap = _sitemap_xml([(f"{_NAVARTIS_BASE}/job/project-manager-200001", "2026-01-01")])
        old_html = _navartis_html(
            title="Project Manager",
            date_posted="2026-01-01T09:00:00.000Z",
            description="<p>Project Manager contract role.</p>",
        )
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(old_html),
        ])
        adapter = _make_navartis_adapter()
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch(since=since))
        assert listings == []

    def test_since_filter_keeps_recent_listings(self):
        sitemap = _sitemap_xml([(f"{_NAVARTIS_BASE}/job/project-manager-200001", "2026-06-20")])
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(_NAVARTIS_HTML),
        ])
        adapter = _make_navartis_adapter()
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch(since=since))
        assert len(listings) == 1

    def test_since_filter_keeps_listing_with_no_date(self):
        sitemap = _sitemap_xml([(f"{_CW_BASE}/job/role-99999", "2026-01-01")])
        no_date_html = _cw_html(title="Project Manager - Highways", date_text="")
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(no_date_html),
        ])
        adapter = _make_cw_adapter()
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch(since=since))
        # Listing has no posted_at → not dropped by since filter
        assert len(listings) == 1

    def test_dedup_by_url(self):
        url = f"{_NAVARTIS_BASE}/job/project-manager-200001"
        sitemap = _sitemap_xml([(url, "2026-06-20"), (url, "2026-06-19")])
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(_NAVARTIS_HTML),
        ])
        adapter = _make_navartis_adapter()
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        # Same URL appears twice in sitemap; should only be fetched/returned once
        assert len(listings) == 1

    def test_sitemap_http_error_returns_empty(self):
        mock = _mock_client([_make_mock_response("", status_code=404)])
        adapter = _make_navartis_adapter()
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert listings == []

    def test_sitemap_request_error_returns_empty(self):
        async def _raise(url, **kwargs):
            raise httpx.ConnectError("refused")

        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        mock.get = AsyncMock(side_effect=_raise)

        adapter = _make_navartis_adapter()
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert listings == []

    def test_job_page_http_error_skips_gracefully(self):
        sitemap = _sitemap_xml([
            (f"{_NAVARTIS_BASE}/job/job-a-100", "2026-06-20"),
            (f"{_NAVARTIS_BASE}/job/job-b-200", "2026-06-19"),
        ])
        # First job page returns 404; second is valid HTML
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response("", status_code=404),
            _make_mock_response(_NAVARTIS_HTML),
        ])
        adapter = _make_navartis_adapter()
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        # First page skipped; second parsed successfully
        assert len(listings) == 1

    def test_max_jobs_limits_fetched_pages(self):
        # 5 URLs in sitemap but max_jobs=2
        entries = [
            (f"{_NAVARTIS_BASE}/job/role-{i}", "2026-06-20")
            for i in range(5)
        ]
        sitemap = _sitemap_xml(entries)
        # We expect sitemap fetch + 2 job page fetches (max_jobs=2)
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(_NAVARTIS_HTML),
            _make_mock_response(_NAVARTIS_HTML),
        ])
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE,
            source_name="navartis",
            crawl_delay=0,
            keywords_list=["project manager"],
            max_jobs=2,
        )
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert mock.get.call_count == 3  # 1 sitemap + 2 job pages

    def test_empty_keywords_returns_all_listings(self):
        sitemap = _sitemap_xml([(f"{_NAVARTIS_BASE}/job/any-role-999", "2026-06-20")])
        mock = _mock_client([
            _make_mock_response(sitemap),
            _make_mock_response(_NAVARTIS_HTML),
        ])
        adapter = VolcanicAdapter(
            base_url=_NAVARTIS_BASE,
            source_name="navartis",
            crawl_delay=0,
            keywords_list=[],
            max_jobs=10,
        )
        with patch("mechpm.adapters.volcanic.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 1
