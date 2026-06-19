"""Unit tests for the CIOB Jobs RSS adapter.

All tests use synthetic data and mocked HTTP — no live network calls.

Coverage (36 tests):
  _parse_pub_date        : RFC 2822, UTC normalisation, None, empty, whitespace, garbage
  _extract_id_from_url   : slug extraction, trailing slash, empty URL fallback
  _strip_html            : tag removal, plain-text passthrough
  _extract_field         : match, no match
  _parse_item            : full mapping, missing title/link, content:encoded
                           preference, description fallback, location/salary/
                           employer extraction, no-content path, metadata fields
  CiobJobsAdapter ctor   : defaults, custom crawl_delay, custom feed_url,
                           extra kwargs accepted
  _parse_feed            : fixture XML (3 items), since filter, no-date always
                           included, dedup, malformed XML, missing channel,
                           empty channel
  fetch()                : happy path, HTTP error, network error, source name
"""
from __future__ import annotations

import asyncio
import pathlib
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.ciob_jobs import (
    CiobJobsAdapter,
    _NS_CONTENT,
    _RE_EMPLOYER,
    _RE_LOCATION,
    _RE_SALARY,
    _SOURCE_NAME,
    _extract_field,
    _extract_id_from_url,
    _parse_item,
    _parse_pub_date,
    _strip_html,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "adapters"
_FEED_FIXTURE = _FIXTURES / "ciob_jobs_feed.xml"


# ---------------------------------------------------------------------------
# Inline minimal RSS helpers
# ---------------------------------------------------------------------------

def _item_xml(
    title: str = "Senior Project Manager",
    link: str = "https://ciobjobs.com/job/senior-project-manager/",
    pub_date: str = "Thu, 19 Jun 2026 08:00:00 +0000",
    description: str = "<p>Brief excerpt.</p>",
    content_encoded: str | None = "<p>Location: London</p><p>Salary: £60,000</p><p>Employer: ABC Ltd</p>",
    guid: str = "https://ciobjobs.com/?p=12345",
) -> str:
    """Build a minimal RSS <item> XML string."""
    content_block = ""
    if content_encoded is not None:
        content_block = (
            f'<content:encoded><![CDATA[{content_encoded}]]></content:encoded>'
        )
    return (
        f'<item '
        f'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<title>{title}</title>"
        f"<link>{link}</link>"
        f"<pubDate>{pub_date}</pubDate>"
        f"<guid>{guid}</guid>"
        f"<description><![CDATA[{description}]]></description>"
        f"{content_block}"
        f"</item>"
    )


def _feed_xml(*item_xmls: str) -> str:
    """Wrap item XML strings in a minimal RSS 2.0 envelope."""
    items = "".join(item_xmls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<channel>"
        "<title>CIOB Jobs</title>"
        "<link>https://ciobjobs.com</link>"
        f"{items}"
        "</channel>"
        "</rss>"
    )


def _mock_http_client(response_text: str, status_code: int = 200) -> AsyncMock:
    """Build a mock httpx.AsyncClient that returns a canned response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = response_text

    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(return_value=mock_response)
    return mock


def _parse_item_from_xml(xml_str: str):
    """Parse a standalone <item> XML string and pass its ET element to _parse_item."""
    import xml.etree.ElementTree as ET
    return _parse_item(ET.fromstring(xml_str))


# ===========================================================================
# _parse_pub_date
# ===========================================================================


class TestParsePubDate:
    def test_rfc2822_utc_zero_offset(self):
        dt = _parse_pub_date("Thu, 19 Jun 2026 08:00:00 +0000")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 19
        assert dt.hour == 8
        assert dt.minute == 0
        assert dt.tzinfo == timezone.utc

    def test_rfc2822_non_utc_normalised_to_utc(self):
        # 10:00 BST (+0100) → 09:00 UTC
        dt = _parse_pub_date("Mon, 15 Jun 2026 10:00:00 +0100")
        assert dt is not None
        assert dt.hour == 9
        assert dt.tzinfo == timezone.utc

    def test_result_is_always_utc_aware(self):
        dt = _parse_pub_date("Fri, 10 Jun 2026 11:00:00 +0000")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_none_returns_none(self):
        assert _parse_pub_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_pub_date("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_pub_date("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_pub_date("not-a-date") is None

    def test_iso_fallback_datetime(self):
        dt = _parse_pub_date("2026-06-15T10:00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 15

    def test_iso_fallback_date_only(self):
        dt = _parse_pub_date("2026-01-20")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 1
        assert dt.day == 20


# ===========================================================================
# _extract_id_from_url
# ===========================================================================


class TestExtractIdFromUrl:
    def test_slug_from_canonical_job_url(self):
        url = "https://ciobjobs.com/job/senior-project-manager-major-infrastructure/"
        slug = _extract_id_from_url(url)
        assert slug == "senior-project-manager-major-infrastructure"

    def test_trailing_slash_is_stripped(self):
        url = "https://ciobjobs.com/job/quantity-surveyor/"
        assert _extract_id_from_url(url) == "quantity-surveyor"

    def test_url_without_trailing_slash(self):
        url = "https://ciobjobs.com/job/project-director"
        assert _extract_id_from_url(url) == "project-director"

    def test_empty_url_returns_hash(self):
        result = _extract_id_from_url("")
        assert len(result) == 16
        assert result.isalnum()

    def test_root_url_returns_hash(self):
        # No slug available from "/" path
        result = _extract_id_from_url("https://ciobjobs.com/")
        assert len(result) == 16

    def test_query_only_url_returns_hash(self):
        # WordPress non-permalink GUID: no path slug
        result = _extract_id_from_url("https://ciobjobs.com/?p=12345")
        assert len(result) == 16


# ===========================================================================
# _strip_html
# ===========================================================================


class TestStripHtml:
    def test_removes_paragraph_tags(self):
        assert "Hello World" in _strip_html("<p>Hello World</p>")

    def test_plain_text_unchanged(self):
        text = "No HTML here"
        result = _strip_html(text)
        assert "No HTML here" in result

    def test_strips_nested_tags(self):
        html = "<strong><em>Bold italic</em></strong>"
        assert "Bold italic" in _strip_html(html)

    def test_empty_string_returns_empty(self):
        assert _strip_html("") == ""


# ===========================================================================
# _extract_field
# ===========================================================================


class TestExtractField:
    def test_location_match(self):
        text = "Location: Manchester, UK"
        result = _extract_field(_RE_LOCATION, text)
        assert result is not None
        assert "Manchester" in result

    def test_salary_match(self):
        text = "Salary: £50,000 - £60,000 per annum"
        result = _extract_field(_RE_SALARY, text)
        assert result is not None
        assert "£50,000" in result

    def test_employer_match(self):
        text = "Employer: ABC Construction Ltd"
        result = _extract_field(_RE_EMPLOYER, text)
        assert result is not None
        assert "ABC Construction" in result

    def test_no_match_returns_none(self):
        assert _extract_field(_RE_LOCATION, "No location here") is None


# ===========================================================================
# _parse_item
# ===========================================================================


class TestParseItem:
    def test_basic_field_mapping(self):
        xml = _item_xml()
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.source == _SOURCE_NAME
        assert listing.title == "Senior Project Manager"
        assert listing.url == "https://ciobjobs.com/job/senior-project-manager/"
        assert listing.agency is None
        assert listing.contract_type_raw is None

    def test_source_listing_id_from_url_slug(self):
        xml = _item_xml(link="https://ciobjobs.com/job/project-director/")
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.source_listing_id == "project-director"

    def test_posted_at_parsed_from_pub_date(self):
        xml = _item_xml(pub_date="Thu, 19 Jun 2026 08:00:00 +0000")
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.posted_at is not None
        assert listing.posted_at.year == 2026
        assert listing.posted_at.month == 6
        assert listing.posted_at.day == 19

    def test_content_encoded_preferred_over_description(self):
        xml = _item_xml(
            description="<p>Short excerpt.</p>",
            content_encoded="<p>Full HTML body with all details.</p>",
        )
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert "Full HTML body" in (listing.description_raw or "")

    def test_description_fallback_when_no_content_encoded(self):
        xml = _item_xml(content_encoded=None)
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert "Brief excerpt" in (listing.description_raw or "")

    def test_location_extracted_from_content(self):
        xml = _item_xml(content_encoded="<p>Location: Birmingham</p>")
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.location_raw is not None
        assert "Birmingham" in listing.location_raw

    def test_salary_extracted_from_content(self):
        xml = _item_xml(content_encoded="<p>Salary: £45,000 - £55,000</p>")
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.salary_raw is not None
        assert "£45,000" in listing.salary_raw

    def test_employer_extracted_from_content(self):
        xml = _item_xml(content_encoded="<p>Employer: Construct Corp Ltd</p>")
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.employer is not None
        assert "Construct Corp" in listing.employer

    def test_no_structured_fields_gives_none_extractions(self):
        xml = _item_xml(content_encoded="<p>Generic description with no labels.</p>")
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.location_raw is None
        assert listing.salary_raw is None
        assert listing.employer is None

    def test_missing_title_returns_none(self):
        xml = _item_xml(title="")
        listing = _parse_item_from_xml(xml)
        assert listing is None

    def test_missing_link_returns_none(self):
        xml = _item_xml(link="")
        listing = _parse_item_from_xml(xml)
        assert listing is None

    def test_guid_stored_in_metadata(self):
        xml = _item_xml(guid="https://ciobjobs.com/?p=99999")
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.metadata.get("guid") == "https://ciobjobs.com/?p=99999"

    def test_pub_date_raw_stored_in_metadata(self):
        pub = "Thu, 19 Jun 2026 08:00:00 +0000"
        xml = _item_xml(pub_date=pub)
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.metadata.get("pub_date_raw") == pub

    def test_empty_content_gives_none_description(self):
        xml = _item_xml(description="", content_encoded=None)
        listing = _parse_item_from_xml(xml)
        assert listing is not None
        assert listing.description_raw is None


# ===========================================================================
# CiobJobsAdapter constructor
# ===========================================================================


class TestCiobJobsAdapterConstructor:
    def test_default_name(self):
        assert CiobJobsAdapter.name == _SOURCE_NAME

    def test_default_crawl_delay(self):
        adapter = CiobJobsAdapter()
        assert adapter.crawl_delay == 2

    def test_default_feed_url(self):
        adapter = CiobJobsAdapter()
        assert adapter.feed_url == "https://ciobjobs.com/feed/"

    def test_custom_crawl_delay(self):
        adapter = CiobJobsAdapter(crawl_delay=5)
        assert adapter.crawl_delay == 5

    def test_custom_feed_url(self):
        adapter = CiobJobsAdapter(feed_url="https://example.com/custom-feed/")
        assert adapter.feed_url == "https://example.com/custom-feed/"

    def test_keywords_list_accepted(self):
        adapter = CiobJobsAdapter(keywords_list=["project manager", "risk manager"])
        assert adapter.keywords_list == ["project manager", "risk manager"]

    def test_extra_kwargs_ignored(self):
        # Orchestrator may pass extra keys from config — must not raise
        adapter = CiobJobsAdapter(unknown_param="ignored")
        assert adapter.name == _SOURCE_NAME


# ===========================================================================
# CiobJobsAdapter._parse_feed  (direct calls — no HTTP)
# ===========================================================================


class TestCiobJobsAdapterParseFeed:
    def test_fixture_parses_three_items(self):
        xml = _FEED_FIXTURE.read_text(encoding="utf-8")
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(xml)
        assert len(listings) == 3

    def test_fixture_first_item_title(self):
        xml = _FEED_FIXTURE.read_text(encoding="utf-8")
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(xml)
        titles = [l.title for l in listings]
        assert any("Senior Project Manager" in t for t in titles)

    def test_fixture_all_have_source_name(self):
        xml = _FEED_FIXTURE.read_text(encoding="utf-8")
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(xml)
        assert all(l.source == _SOURCE_NAME for l in listings)

    def test_since_filter_excludes_old_items(self):
        feed = _feed_xml(
            _item_xml(
                title="Old Job",
                link="https://ciobjobs.com/job/old-job/",
                pub_date="Mon, 01 Jun 2026 09:00:00 +0000",
            ),
            _item_xml(
                title="New Job",
                link="https://ciobjobs.com/job/new-job/",
                pub_date="Thu, 19 Jun 2026 08:00:00 +0000",
            ),
        )
        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(feed, since=since)
        assert len(listings) == 1
        assert listings[0].title == "New Job"

    def test_since_filter_includes_items_on_boundary(self):
        feed = _feed_xml(
            _item_xml(
                title="Exact Match",
                link="https://ciobjobs.com/job/exact-match/",
                pub_date="Wed, 10 Jun 2026 00:00:00 +0000",
            ),
        )
        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(feed, since=since)
        assert len(listings) == 1

    def test_no_posted_at_always_included_despite_since(self):
        feed = _feed_xml(
            _item_xml(
                title="No Date Job",
                link="https://ciobjobs.com/job/no-date/",
                pub_date="",
            ),
        )
        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(feed, since=since)
        assert len(listings) == 1

    def test_deduplication_by_slug(self):
        dup_item = _item_xml(
            title="Duplicate Role",
            link="https://ciobjobs.com/job/duplicate-role/",
        )
        feed = _feed_xml(dup_item, dup_item)
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(feed)
        assert len(listings) == 1

    def test_malformed_xml_returns_empty_list(self):
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed("<rss><channel><item>UNCLOSED")
        assert listings == []

    def test_missing_channel_returns_empty_list(self):
        xml = '<?xml version="1.0"?><rss version="2.0"></rss>'
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(xml)
        assert listings == []

    def test_empty_channel_returns_empty_list(self):
        xml = (
            '<?xml version="1.0"?>'
            '<rss version="2.0"><channel></channel></rss>'
        )
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(xml)
        assert listings == []

    def test_items_missing_title_are_skipped(self):
        feed = _feed_xml(
            _item_xml(title=""),
            _item_xml(title="Valid Job", link="https://ciobjobs.com/job/valid-job/"),
        )
        adapter = CiobJobsAdapter()
        listings = adapter._parse_feed(feed)
        assert len(listings) == 1
        assert listings[0].title == "Valid Job"


# ===========================================================================
# CiobJobsAdapter.fetch()  (mocked HTTP)
# ===========================================================================


class TestCiobJobsAdapterFetch:
    @patch("httpx.AsyncClient")
    def test_fetch_happy_path_returns_listings(self, mock_client_cls):
        xml = _FEED_FIXTURE.read_text(encoding="utf-8")
        mock_client_cls.return_value = _mock_http_client(xml)

        adapter = CiobJobsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert len(listings) == 3

    @patch("httpx.AsyncClient")
    def test_fetch_source_name_is_ciob_jobs(self, mock_client_cls):
        xml = _FEED_FIXTURE.read_text(encoding="utf-8")
        mock_client_cls.return_value = _mock_http_client(xml)

        adapter = CiobJobsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert all(l.source == _SOURCE_NAME for l in listings)

    @patch("httpx.AsyncClient")
    def test_fetch_since_filter_applied(self, mock_client_cls):
        feed = _feed_xml(
            _item_xml(
                title="Recent Job",
                link="https://ciobjobs.com/job/recent-job/",
                pub_date="Thu, 19 Jun 2026 08:00:00 +0000",
            ),
            _item_xml(
                title="Old Job",
                link="https://ciobjobs.com/job/old-job/",
                pub_date="Mon, 01 Jun 2026 09:00:00 +0000",
            ),
        )
        mock_client_cls.return_value = _mock_http_client(feed)

        since = datetime(2026, 6, 10, tzinfo=timezone.utc)
        adapter = CiobJobsAdapter()
        listings = asyncio.run(adapter.fetch(since=since))

        assert len(listings) == 1
        assert listings[0].title == "Recent Job"

    @patch("httpx.AsyncClient")
    def test_fetch_http_error_returns_empty_list(self, mock_client_cls):
        mock_client_cls.return_value = _mock_http_client("", status_code=503)

        adapter = CiobJobsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("httpx.AsyncClient")
    def test_fetch_request_error_returns_empty_list(self, mock_client_cls):
        import httpx as _httpx

        async def _get_raises(url, **kwargs):
            raise _httpx.RequestError("connection refused")

        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        mock.get = AsyncMock(side_effect=_get_raises)
        mock_client_cls.return_value = mock

        adapter = CiobJobsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("httpx.AsyncClient")
    def test_fetch_malformed_xml_returns_empty_list(self, mock_client_cls):
        mock_client_cls.return_value = _mock_http_client("<rss><BROKEN")

        adapter = CiobJobsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []

    @patch("httpx.AsyncClient")
    def test_fetch_empty_feed_returns_empty_list(self, mock_client_cls):
        empty = (
            '<?xml version="1.0"?>'
            '<rss version="2.0"><channel></channel></rss>'
        )
        mock_client_cls.return_value = _mock_http_client(empty)

        adapter = CiobJobsAdapter()
        listings = asyncio.run(adapter.fetch())

        assert listings == []
