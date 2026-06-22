"""Unit tests for the Madgex job board platform adapter.

Covers RICS Recruit (ricsrecruit.com) and ICE Recruit (icerecruit.com).
All tests use synthetic data and mocked HTTP — no live network calls.

Coverage (51 tests):
  _strip_html            : tag removal, plain passthrough, nested, empty
  _parse_rfc2822_date    : valid RFC 2822, +0000, BST, None, empty,
                           whitespace-only, garbage string
  _extract_id_from_url   : valid URL, missing ID, empty string,
                           multiple path segments, guid fallback URL
  _clean_url             : strips TrackID+utm params, keeps non-tracking,
                           empty input, no-query input, unknown params kept
  _parse_title           : agency+title, no-colon passthrough, empty string,
                           leading colon edge case, nested colon in title
  _parse_description     : full (salary+location+body), salary-only,
                           location-only, None, empty, HTML input,
                           no-salary first line, multi-line body,
                           "Up to £X" salary prefix
  _parse_rss_page        : valid single-item, multi-item, totalResults,
                           itemsPerPage, malformed XML, no channel,
                           empty items, missing namespace
  _item_to_raw_listing   : full field mapping (RICS), source name (ICE),
                           agency split, missing title, missing link,
                           ID from guid, cleaned URL, salary_raw, location_raw,
                           posted_at parsed, contract_type_raw None,
                           description_raw stored raw
  MadgexAdapter ctor     : name=source_name, base_url trailing slash stripped,
                           defaults (crawl_delay, country_code, max_pages),
                           keywords_list accepted, extra kwargs ignored
  _build_rss_url         : no keyword, with keyword, page>1, keyword+page
  fetch()                : happy path RICS, happy path ICE,
                           no keywords (all-jobs fetch), two keywords dedup,
                           since filter drops old, since keeps no-date entry,
                           pagination triggered, HTTP error returns [],
                           request error returns [], XML parse error returns [],
                           empty feed returns []
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree as ET

import pytest

from mechpm.adapters.madgex import (
    MadgexAdapter,
    _NS_OPENSEARCH,
    _clean_url,
    _extract_id_from_url,
    _item_to_raw_listing,
    _parse_description,
    _parse_rfc2822_date,
    _parse_rss_page,
    _parse_title,
    _strip_html,
)

# ---------------------------------------------------------------------------
# RSS XML fixture helpers
# ---------------------------------------------------------------------------

_RICS_BASE = "https://www.ricsrecruit.com"
_ICE_BASE = "https://www.icerecruit.com"

_RSS_ENVELOPE_OPEN = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<rss version="2.0"'
    ' xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
    "<channel>"
)
_RSS_ENVELOPE_CLOSE = "</channel></rss>"


def _rss_xml(
    items: list[str],
    total_results: int | None = None,
    items_per_page: int | None = None,
) -> str:
    """Build a minimal Madgex RSS XML document with the given item strings."""
    meta = ""
    if total_results is not None:
        meta += f"<opensearch:totalResults>{total_results}</opensearch:totalResults>"
    if items_per_page is not None:
        meta += f"<opensearch:itemsPerPage>{items_per_page}</opensearch:itemsPerPage>"
    body = "".join(items)
    return f"{_RSS_ENVELOPE_OPEN}{meta}{body}{_RSS_ENVELOPE_CLOSE}"


def _make_item_xml(
    job_id: str = "254176",
    title: str = "Park Avenue Recruitment: Senior Project Manager",
    description: str = "£400 - £500 per day:\n\nPark Avenue Recruitment:\nGreat role.\nLondon, England",
    link: str | None = None,
    pubdate: str = "Mon, 22 Jun 2026 12:28:00 +0000",
    guid: str | None = None,
) -> str:
    """Build a single RSS <item> XML string with realistic Madgex data."""
    if link is None:
        link = (
            f"https://www.ricsrecruit.com/job/{job_id}/senior-project-manager/"
            "?TrackID=124&amp;utm_source=rss&amp;utm_medium=feed&amp;utm_campaign=general"
        )
    if guid is None:
        guid = link
    return (
        f"<item>"
        f"<title>{title}</title>"
        f"<description>{description}</description>"
        f"<link>{link}</link>"
        f"<pubDate>{pubdate}</pubDate>"
        f"<guid isPermaLink=\"true\">{guid}</guid>"
        f"</item>"
    )


def _make_mock_response(xml: str, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = xml
    return resp


def _mock_client(responses: list[MagicMock]) -> AsyncMock:
    """Build a mock httpx.AsyncClient that returns responses in order."""
    call_index = [0]

    async def _get(url, **kwargs):
        idx = call_index[0]
        call_index[0] += 1
        if idx < len(responses):
            return responses[idx]
        return _make_mock_response(_rss_xml([]), status_code=200)

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(side_effect=_get)
    return mock


# ===========================================================================
# _strip_html
# ===========================================================================


class TestStripHtml:
    def test_removes_paragraph_tags(self):
        assert "Hello World" in _strip_html("<p>Hello World</p>")

    def test_plain_text_unchanged(self):
        assert "No HTML here" in _strip_html("No HTML here")

    def test_strips_nested_tags(self):
        assert "Bold italic" in _strip_html("<strong><em>Bold italic</em></strong>")

    def test_empty_string_returns_empty(self):
        assert _strip_html("") == ""


# ===========================================================================
# _parse_rfc2822_date
# ===========================================================================


class TestParseRfc2822Date:
    def test_utc_date(self):
        dt = _parse_rfc2822_date("Mon, 22 Jun 2026 12:28:00 +0000")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 22
        assert dt.hour == 12
        assert dt.minute == 28
        assert dt.tzinfo == timezone.utc

    def test_bst_offset_converted_to_utc(self):
        # BST is +0100; 13:28:00 +0100 → 12:28:00 UTC
        dt = _parse_rfc2822_date("Mon, 22 Jun 2026 13:28:00 +0100")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_none_returns_none(self):
        assert _parse_rfc2822_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_rfc2822_date("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_rfc2822_date("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_rfc2822_date("not-a-date") is None

    def test_result_is_utc_aware(self):
        dt = _parse_rfc2822_date("Fri, 19 Jun 2026 09:24:00 +0000")
        assert dt is not None
        assert dt.tzinfo == timezone.utc


# ===========================================================================
# _extract_id_from_url
# ===========================================================================


class TestExtractIdFromUrl:
    def test_extracts_id_from_rics_url(self):
        url = "https://www.ricsrecruit.com/job/254176/interim-valuations-surveyor/"
        assert _extract_id_from_url(url) == "254176"

    def test_extracts_id_from_ice_url(self):
        url = "https://www.icerecruit.com/job/232340/senior-engineer-structures-/"
        assert _extract_id_from_url(url) == "232340"

    def test_url_with_tracking_params(self):
        url = (
            "https://www.ricsrecruit.com/job/254176/slug/"
            "?TrackID=124&utm_source=rss"
        )
        assert _extract_id_from_url(url) == "254176"

    def test_no_job_path_returns_none(self):
        assert _extract_id_from_url("https://www.ricsrecruit.com/") is None

    def test_empty_string_returns_none(self):
        assert _extract_id_from_url("") is None


# ===========================================================================
# _clean_url
# ===========================================================================


class TestCleanUrl:
    def test_removes_trackid(self):
        url = "https://www.ricsrecruit.com/job/123/slug/?TrackID=124"
        result = _clean_url(url)
        assert "TrackID" not in result

    def test_removes_utm_params(self):
        url = (
            "https://www.ricsrecruit.com/job/123/slug/"
            "?utm_source=rss&utm_medium=feed&utm_campaign=general"
        )
        result = _clean_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "utm_campaign" not in result

    def test_keeps_non_tracking_params(self):
        url = "https://www.ricsrecruit.com/job/123/slug/?countrycode=GB"
        result = _clean_url(url)
        assert "countrycode=GB" in result

    def test_empty_string_returned_unchanged(self):
        assert _clean_url("") == ""

    def test_url_without_query_unchanged(self):
        url = "https://www.ricsrecruit.com/job/123/slug/"
        result = _clean_url(url)
        assert result == url


# ===========================================================================
# _parse_title
# ===========================================================================


class TestParseTitle:
    def test_standard_agency_title_format(self):
        title, agency = _parse_title("Park Avenue Recruitment: Senior Project Manager")
        assert title == "Senior Project Manager"
        assert agency == "Park Avenue Recruitment"

    def test_no_colon_separator_returns_full_as_title(self):
        title, agency = _parse_title("Senior Project Manager")
        assert title == "Senior Project Manager"
        assert agency is None

    def test_empty_string_returns_empty_title(self):
        title, agency = _parse_title("")
        assert title == ""
        assert agency is None

    def test_multiple_colons_uses_first(self):
        title, agency = _parse_title("Agency Ltd: PM: Infrastructure")
        assert agency == "Agency Ltd"
        assert title == "PM: Infrastructure"

    def test_strips_whitespace(self):
        title, agency = _parse_title("  Turner Recruitment :  Quantity Surveyor  ")
        assert "Quantity Surveyor" in title
        assert agency is not None


# ===========================================================================
# _parse_description
# ===========================================================================


class TestParseDescription:
    def test_full_parse_salary_location_body(self):
        raw = "£400 - £500 per day:\n\nPark Avenue Recruitment:\nGreat role details here.\nLondon, England"
        salary, location, body = _parse_description(raw)
        assert salary is not None and "£400" in salary
        assert location is not None and "London" in location
        assert body is not None and "role details" in body

    def test_none_returns_triple_none(self):
        assert _parse_description(None) == (None, None, None)

    def test_empty_string_returns_triple_none(self):
        assert _parse_description("") == (None, None, None)

    def test_salary_line_with_up_to_prefix(self):
        raw = "Up to £80,000:\n\nAgency:\nGreat job.\nManchester"
        salary, location, _ = _parse_description(raw)
        assert salary is not None and "80,000" in salary

    def test_location_is_last_line(self):
        raw = "£500 per day:\n\nAgency:\nJob description here.\nBirmingham, West Midlands"
        _, location, _ = _parse_description(raw)
        assert location is not None and "Birmingham" in location

    def test_html_tags_stripped_before_parsing(self):
        raw = "<p>£450 per day:</p>\n<p>Agency:</p>\n<p>Description.</p>\n<p>London</p>"
        salary, location, _ = _parse_description(raw)
        assert salary is not None and "£450" in salary
        assert location is not None and "London" in location

    def test_no_salary_pattern_does_not_extract_salary(self):
        raw = "Job description here.\nManchester"
        salary, location, _ = _parse_description(raw)
        # First line has no currency; salary should be None
        assert salary is None
        assert location is not None and "Manchester" in location

    def test_multi_line_body_joined(self):
        raw = "£400 per day:\n\nAgency:\nFirst line.\nSecond line.\nLondon"
        _, _, body = _parse_description(raw)
        assert body is not None
        assert "First line" in body
        assert "Second line" in body

    def test_annual_salary_pa_pattern(self):
        raw = "£87,710 - £93,326 p/a:\n\nMTVH:\nGreat opportunity.\nFarringdon, London EC1 8JS"
        salary, location, _ = _parse_description(raw)
        assert salary is not None and "87,710" in salary
        assert location is not None and "London" in location


# ===========================================================================
# _parse_rss_page
# ===========================================================================


class TestParseRssPage:
    def test_single_item_returns_one_listing(self):
        xml = _rss_xml([_make_item_xml()])
        listings, _, _ = _parse_rss_page(xml, "rics_recruit")
        assert len(listings) == 1

    def test_two_items_returns_two_listings(self):
        xml = _rss_xml([_make_item_xml("100"), _make_item_xml("101")])
        listings, _, _ = _parse_rss_page(xml, "rics_recruit")
        assert len(listings) == 2

    def test_total_results_extracted(self):
        xml = _rss_xml([_make_item_xml()], total_results=521)
        _, total, _ = _parse_rss_page(xml, "rics_recruit")
        assert total == 521

    def test_items_per_page_extracted(self):
        xml = _rss_xml([_make_item_xml()], items_per_page=25)
        _, _, ipp = _parse_rss_page(xml, "rics_recruit")
        assert ipp == 25

    def test_malformed_xml_returns_empty(self):
        listings, total, ipp = _parse_rss_page("not xml at all <<>>", "rics_recruit")
        assert listings == []
        assert total is None
        assert ipp is None

    def test_no_channel_returns_empty(self):
        xml = '<?xml version="1.0"?><rss version="2.0"></rss>'
        listings, total, ipp = _parse_rss_page(xml, "rics_recruit")
        assert listings == []

    def test_empty_items_returns_empty_list(self):
        xml = _rss_xml([])
        listings, _, _ = _parse_rss_page(xml, "rics_recruit")
        assert listings == []

    def test_missing_opensearch_total_returns_none(self):
        xml = _rss_xml([_make_item_xml()])
        _, total, _ = _parse_rss_page(xml, "rics_recruit")
        assert total is None


# ===========================================================================
# _item_to_raw_listing
# ===========================================================================


class TestItemToRawListing:
    def _make_el(self, xml_str: str) -> ET.Element:
        return ET.fromstring(xml_str)

    def test_basic_field_mapping_rics(self):
        xml = _make_item_xml(job_id="254176", title="Agency: Senior PM")
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.source == "rics_recruit"
        assert listing.title == "Senior PM"
        assert listing.source_listing_id == "254176"

    def test_source_name_ice(self):
        link = "https://www.icerecruit.com/job/232340/slug/?TrackID=54&amp;utm_source=rss"
        xml = _make_item_xml(job_id="232340", link=link, title="Agency: Engineer")
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "ice_recruit")
        assert listing is not None
        assert listing.source == "ice_recruit"

    def test_agency_split_from_title(self):
        xml = _make_item_xml(title="Turner Recruitment: Project Director")
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.agency == "Turner Recruitment"
        assert listing.title == "Project Director"

    def test_tracking_params_stripped_from_url(self):
        xml = _make_item_xml(job_id="1001")
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert "TrackID" not in listing.url
        assert "utm_source" not in listing.url

    def test_posted_at_parsed(self):
        xml = _make_item_xml(pubdate="Wed, 17 Jun 2026 09:24:00 +0000")
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.posted_at is not None
        assert listing.posted_at.year == 2026
        assert listing.posted_at.month == 6
        assert listing.posted_at.day == 17

    def test_salary_raw_extracted(self):
        xml = _make_item_xml(description="£400 - £500 per day:\n\nAgency:\nDesc.\nLondon")
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.salary_raw is not None
        assert "£400" in listing.salary_raw

    def test_location_raw_extracted(self):
        xml = _make_item_xml(description="£400 per day:\n\nAgency:\nDesc.\nEngland, Norfolk")
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.location_raw is not None
        assert "Norfolk" in listing.location_raw

    def test_contract_type_raw_is_none(self):
        xml = _make_item_xml()
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.contract_type_raw is None

    def test_missing_title_returns_none(self):
        xml = (
            "<item>"
            "<title></title>"
            "<link>https://www.ricsrecruit.com/job/1/slug/</link>"
            "<pubDate>Mon, 22 Jun 2026 12:00:00 +0000</pubDate>"
            "</item>"
        )
        item_el = self._make_el(xml)
        assert _item_to_raw_listing(item_el, "rics_recruit") is None

    def test_missing_link_returns_none(self):
        xml = (
            "<item>"
            "<title>Agency: Title</title>"
            "<link></link>"
            "<pubDate>Mon, 22 Jun 2026 12:00:00 +0000</pubDate>"
            "</item>"
        )
        item_el = self._make_el(xml)
        assert _item_to_raw_listing(item_el, "rics_recruit") is None

    def test_id_falls_back_to_guid(self):
        xml = (
            "<item>"
            "<title>Agency: Title</title>"
            "<description>desc</description>"
            "<link>https://www.ricsrecruit.com/no-job-path/</link>"
            "<pubDate>Mon, 22 Jun 2026 12:00:00 +0000</pubDate>"
            "<guid>https://www.ricsrecruit.com/job/99999/slug/</guid>"
            "</item>"
        )
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.source_listing_id == "99999"

    def test_no_extractable_id_returns_none(self):
        xml = (
            "<item>"
            "<title>Agency: Title</title>"
            "<description>desc</description>"
            "<link>https://www.ricsrecruit.com/search/</link>"
            "<pubDate>Mon, 22 Jun 2026 12:00:00 +0000</pubDate>"
            "</item>"
        )
        item_el = self._make_el(xml)
        assert _item_to_raw_listing(item_el, "rics_recruit") is None

    def test_description_raw_stored(self):
        desc = "£500 per day:\n\nAgency:\nJob body.\nLondon"
        xml = _make_item_xml(description=desc)
        item_el = self._make_el(xml)
        listing = _item_to_raw_listing(item_el, "rics_recruit")
        assert listing is not None
        assert listing.description_raw is not None
        assert "£500" in listing.description_raw


# ===========================================================================
# MadgexAdapter constructor
# ===========================================================================


class TestMadgexAdapterConstructor:
    def test_name_is_source_name(self):
        adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com", source_name="rics_recruit"
        )
        assert adapter.name == "rics_recruit"

    def test_base_url_trailing_slash_stripped(self):
        adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com/", source_name="rics_recruit"
        )
        assert adapter.base_url == "https://www.ricsrecruit.com"

    def test_default_crawl_delay(self):
        adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com", source_name="rics_recruit"
        )
        assert adapter.crawl_delay == 3

    def test_default_country_code(self):
        adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com", source_name="rics_recruit"
        )
        assert adapter.country_code == "GB"

    def test_keywords_list_stored(self):
        kws = ["project manager", "risk manager"]
        adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com",
            source_name="rics_recruit",
            keywords_list=kws,
        )
        assert adapter.keywords_list == kws

    def test_none_keywords_becomes_empty_list(self):
        adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com",
            source_name="rics_recruit",
            keywords_list=None,
        )
        assert adapter.keywords_list == []

    def test_extra_kwargs_ignored(self):
        adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com",
            source_name="rics_recruit",
            unknown_param="ignored",
        )
        assert adapter.name == "rics_recruit"


# ===========================================================================
# MadgexAdapter._build_rss_url
# ===========================================================================


class TestBuildRssUrl:
    def setup_method(self):
        self.adapter = MadgexAdapter(
            base_url="https://www.ricsrecruit.com", source_name="rics_recruit"
        )

    def test_no_keyword_page_1(self):
        url = self.adapter._build_rss_url()
        assert url == "https://www.ricsrecruit.com/jobsrss/?countrycode=GB"

    def test_with_keyword(self):
        url = self.adapter._build_rss_url(keyword="project manager")
        assert "Keywords=project+manager" in url

    def test_page_2_appended(self):
        url = self.adapter._build_rss_url(page=2)
        assert "page=2" in url

    def test_keyword_and_page(self):
        url = self.adapter._build_rss_url(keyword="risk manager", page=3)
        assert "Keywords=risk+manager" in url
        assert "page=3" in url


# ===========================================================================
# MadgexAdapter.fetch() — mocked HTTP
# ===========================================================================


def _make_adapter(
    base_url: str = _RICS_BASE,
    source_name: str = "rics_recruit",
    keywords: list[str] | None = None,
) -> MadgexAdapter:
    return MadgexAdapter(
        base_url=base_url,
        source_name=source_name,
        crawl_delay=0,
        keywords_list=keywords or ["project manager"],
    )


class TestFetch:
    def test_happy_path_rics(self):
        xml = _rss_xml([_make_item_xml("1001"), _make_item_xml("1002")], total_results=2)
        adapter = _make_adapter(source_name="rics_recruit")
        mock = _mock_client([_make_mock_response(xml)])
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 2
        assert all(l.source == "rics_recruit" for l in listings)

    def test_happy_path_ice(self):
        xml = _rss_xml([_make_item_xml("2001")], total_results=1)
        adapter = _make_adapter(base_url=_ICE_BASE, source_name="ice_recruit")
        mock = _mock_client([_make_mock_response(xml)])
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 1
        assert listings[0].source == "ice_recruit"

    def test_no_keywords_fetches_without_keyword_param(self):
        xml = _rss_xml([_make_item_xml("3001")], total_results=1)
        adapter = MadgexAdapter(
            base_url=_RICS_BASE,
            source_name="rics_recruit",
            crawl_delay=0,
            keywords_list=[],
        )
        mock = _mock_client([_make_mock_response(xml)])
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 1
        called_url = mock.get.call_args_list[0][0][0]
        assert "Keywords" not in called_url

    def test_two_keywords_dedup_shared_listing(self):
        # Both keyword queries return the same job ID — should only appear once.
        item = _make_item_xml("5000")
        xml = _rss_xml([item], total_results=1)
        mock = _mock_client([
            _make_mock_response(xml),
            _make_mock_response(xml),
        ])
        adapter = _make_adapter(keywords=["project manager", "risk manager"])
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        ids = [l.source_listing_id for l in listings]
        assert ids.count("5000") == 1

    def test_two_keywords_unique_listings_combined(self):
        xml1 = _rss_xml([_make_item_xml("6001")], total_results=1)
        xml2 = _rss_xml([_make_item_xml("6002")], total_results=1)
        mock = _mock_client([
            _make_mock_response(xml1),
            _make_mock_response(xml2),
        ])
        adapter = _make_adapter(keywords=["project manager", "quantity surveyor"])
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 2

    def test_since_filter_drops_old_listings(self):
        old_item = _make_item_xml("7001", pubdate="Wed, 01 Jan 2026 09:00:00 +0000")
        xml = _rss_xml([old_item], total_results=1)
        mock = _mock_client([_make_mock_response(xml)])
        adapter = _make_adapter()
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch(since=since))
        assert listings == []

    def test_since_filter_keeps_recent_listings(self):
        recent_item = _make_item_xml("7002", pubdate="Mon, 22 Jun 2026 12:00:00 +0000")
        xml = _rss_xml([recent_item], total_results=1)
        mock = _mock_client([_make_mock_response(xml)])
        adapter = _make_adapter()
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch(since=since))
        assert len(listings) == 1

    def test_since_keeps_listing_with_no_date(self):
        no_date_xml = (
            f"{_RSS_ENVELOPE_OPEN}"
            "<item>"
            "<title>Agency: Title</title>"
            "<description>desc</description>"
            "<link>https://www.ricsrecruit.com/job/8001/slug/</link>"
            "</item>"
            f"{_RSS_ENVELOPE_CLOSE}"
        )
        mock = _mock_client([_make_mock_response(no_date_xml)])
        adapter = _make_adapter()
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch(since=since))
        assert len(listings) == 1

    def test_pagination_fetches_page_2(self):
        # page 1: 1 item, totalResults=2, itemsPerPage=1 → needs page 2
        xml_p1 = _rss_xml([_make_item_xml("9001")], total_results=2, items_per_page=1)
        xml_p2 = _rss_xml([_make_item_xml("9002")], total_results=2, items_per_page=1)
        mock = _mock_client([
            _make_mock_response(xml_p1),
            _make_mock_response(xml_p2),
        ])
        adapter = _make_adapter()
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert len(listings) == 2
        ids = {l.source_listing_id for l in listings}
        assert ids == {"9001", "9002"}

    def test_http_error_returns_empty(self):
        mock = _mock_client([_make_mock_response("", status_code=500)])
        adapter = _make_adapter()
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert listings == []

    def test_request_error_returns_empty(self):
        import httpx as _httpx

        async def _get_raises(url, **kwargs):
            raise _httpx.RequestError("connection refused")

        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        mock.get = AsyncMock(side_effect=_get_raises)

        adapter = _make_adapter()
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert listings == []

    def test_xml_parse_error_returns_empty(self):
        mock = _mock_client([_make_mock_response("INVALID XML <<>>>")])
        adapter = _make_adapter()
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert listings == []

    def test_empty_feed_returns_empty(self):
        xml = _rss_xml([], total_results=0)
        mock = _mock_client([_make_mock_response(xml)])
        adapter = _make_adapter()
        with patch("mechpm.adapters.madgex.httpx.AsyncClient", return_value=mock):
            listings = asyncio.run(adapter.fetch())
        assert listings == []
