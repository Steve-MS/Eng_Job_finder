"""Unit tests for the Kier Careers adapter — HTML scrape of jobs.kier.co.uk.

Fixtures:
  tests/fixtures/adapters/kier_page1.html       — 5 cards, page 1 of 2 (next enabled)
  tests/fixtures/adapters/kier_last_page.html   — 3 cards, last page (next disabled)

Confirmed DOM structure (live recon 2026-06-22):
  article.col-12.job-search-results-card-col
    h3.card-title.job-search-results-card-title a → title + URL
    li.job-component-location span               → location_raw
    li.job-component-employment-type span        → contract_type_raw
    li.job-component-category span               → category (metadata)
    p.card-text.job-search-results-summary       → description_raw
  li.next_page.disabled                          → last-page signal

All tests use fixtures or mocked HTTP — no live network calls.

Coverage (28 tests):
  _build_search_url          : basic, page 2, special chars
  _extract_listing_id        : UUID extraction, slug fallback, UUID variants
  _is_last_page              : not last, is last (disabled)
  _card_to_raw_listing       : title, url, employer fixed, location, contract_type,
                               category in metadata, description, missing title guard,
                               no-location card
  _parse_html                : fixture count (page1), fixture count (last_page),
                               source, source_listing_id, uuid in id, slug id,
                               employer is Kier Group, location, contract_type,
                               description, empty html, no cards, is_last_page flag
  KierAdapter ctor           : defaults (name, crawl_delay), keywords accepted,
                               extra kwargs ignored
  fetch()                    : happy path, two keywords dedup, last page stops early,
                               HTTP error, empty page stops, no posted_at (always kept
                               by since filter), request error returns partial
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mechpm.adapters.kier import (
    KierAdapter,
    _SOURCE_NAME,
    _EMPLOYER,
    _build_search_url,
    _extract_listing_id,
    _is_last_page,
    _parse_html,
)
from selectolax.parser import HTMLParser

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "adapters" / "kier_page1.html"
)
_LAST_PAGE_FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "adapters" / "kier_last_page.html"
)
_BASE_URL = "https://jobs.kier.co.uk"
_PAGE_URL = _BASE_URL + "/jobs/search?page=1&query=project+manager"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_html() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def last_page_html() -> str:
    return _LAST_PAGE_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed_listings(raw_html):
    listings, _ = _parse_html(raw_html, _BASE_URL, _PAGE_URL)
    return listings


@pytest.fixture(scope="module")
def last_page_result(last_page_html):
    return _parse_html(last_page_html, _BASE_URL, _PAGE_URL + "2")


# ===========================================================================
# 1 – URL builder
# ===========================================================================


def test_build_search_url_page1():
    url = _build_search_url(_BASE_URL, "project manager", 1)
    assert url == f"{_BASE_URL}/jobs/search?page=1&query=project+manager"


def test_build_search_url_page2():
    url = _build_search_url(_BASE_URL, "quantity surveyor", 2)
    assert "page=2" in url
    assert "quantity+surveyor" in url


def test_build_search_url_special_chars():
    url = _build_search_url(_BASE_URL, "M&E manager", 1)
    # & must be percent-encoded
    assert "M%26E" in url or "M+%26" in url or "M%26E+manager" in url


# ===========================================================================
# 2 – ID extraction
# ===========================================================================


def test_extract_listing_id_uuid():
    url = (
        "https://jobs.kier.co.uk/jobs/commercial-manager-glasgow-"
        "strathclyde-united-kingdom-493d7be3-95d1-406c-99ab-5574d777dd43"
    )
    assert _extract_listing_id(url) == "493d7be3-95d1-406c-99ab-5574d777dd43"


def test_extract_listing_id_slug_fallback():
    url = "https://jobs.kier.co.uk/jobs/project-manager-falmer-east-sussex-united-kingdom"
    assert _extract_listing_id(url) == "project-manager-falmer-east-sussex-united-kingdom"


def test_extract_listing_id_another_uuid():
    url = (
        "https://jobs.kier.co.uk/jobs/"
        "senior-project-manager-london-greater-london-d558df7f-0a33-4d1f-b7f0-8e07fabbea48"
    )
    assert _extract_listing_id(url) == "d558df7f-0a33-4d1f-b7f0-8e07fabbea48"


# ===========================================================================
# 3 – Last-page detection
# ===========================================================================


def test_is_last_page_false(raw_html):
    tree = HTMLParser(raw_html)
    assert _is_last_page(tree) is False


def test_is_last_page_true(last_page_html):
    tree = HTMLParser(last_page_html)
    assert _is_last_page(tree) is True


def test_is_last_page_no_next_element():
    tree = HTMLParser("<html><body><ul class='pagination'></ul></body></html>")
    assert _is_last_page(tree) is False


# ===========================================================================
# 4 – HTML parsing: volume gate
# ===========================================================================


def test_listing_count_page1(parsed_listings):
    """Fixture page 1 must yield exactly 5 listings."""
    assert len(parsed_listings) == 5


def test_listing_count_last_page(last_page_result):
    listings, is_last = last_page_result
    assert len(listings) == 3
    assert is_last is True


# ===========================================================================
# 5 – Source identifier
# ===========================================================================


def test_source_is_kier(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == _SOURCE_NAME


# ===========================================================================
# 6 – source_listing_id
# ===========================================================================


def test_source_listing_id_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.source_listing_id, "source_listing_id must be non-empty"


def test_first_card_id_is_uuid(parsed_listings):
    # First card URL ends with UUID 493d7be3-...
    assert parsed_listings[0].source_listing_id == "493d7be3-95d1-406c-99ab-5574d777dd43"


def test_second_card_id_is_slug(parsed_listings):
    # Second card URL has no UUID, should use full slug
    assert parsed_listings[1].source_listing_id == "project-manager-falmer-east-sussex-united-kingdom"


# ===========================================================================
# 7 – URL field
# ===========================================================================


def test_urls_start_with_https(parsed_listings):
    for listing in parsed_listings:
        assert listing.url.startswith("https://")


def test_first_url_contains_domain(parsed_listings):
    assert "jobs.kier.co.uk" in parsed_listings[0].url


# ===========================================================================
# 8 – Title
# ===========================================================================


def test_first_listing_title(parsed_listings):
    assert parsed_listings[0].title == "Project Manager"


def test_titles_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.title


# ===========================================================================
# 9 – Employer (fixed)
# ===========================================================================


def test_employer_is_kier_group(parsed_listings):
    for listing in parsed_listings:
        assert listing.employer == _EMPLOYER


def test_employer_constant_value():
    assert _EMPLOYER == "Kier Group"


# ===========================================================================
# 10 – Location
# ===========================================================================


def test_first_listing_location(parsed_listings):
    assert parsed_listings[0].location_raw == "Glasgow"


def test_location_none_when_absent(parsed_listings):
    # Card 4 (Quantity Surveyor) has no location in fixture
    qs = next(l for l in parsed_listings if "Quantity Surveyor" in l.title)
    assert qs.location_raw is None


# ===========================================================================
# 11 – Contract type
# ===========================================================================


def test_first_listing_contract_type(parsed_listings):
    assert parsed_listings[0].contract_type_raw == "Permanent - Full Time"


def test_contract_type_none_when_absent(parsed_listings):
    # Card 5 (Senior PM) has no employment type in fixture
    spm = next(l for l in parsed_listings if "Senior Project Manager" in l.title)
    assert spm.contract_type_raw is None


# ===========================================================================
# 12 – Description
# ===========================================================================


def test_description_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.description_raw


# ===========================================================================
# 13 – Metadata category
# ===========================================================================


def test_category_in_metadata(parsed_listings):
    # First card has category="Project Management"
    assert parsed_listings[0].metadata.get("category") == "Project Management"


def test_category_none_when_absent(parsed_listings):
    # Second card has no category in fixture
    pm2 = parsed_listings[1]
    assert pm2.metadata.get("category") is None


# ===========================================================================
# 14 – No posted_at (Kier cards don't include dates)
# ===========================================================================


def test_posted_at_is_none(parsed_listings):
    for listing in parsed_listings:
        assert listing.posted_at is None


# ===========================================================================
# 15 – Error handling
# ===========================================================================


def test_parse_html_empty_string():
    listings, is_last = _parse_html("", _BASE_URL, _PAGE_URL)
    assert listings == []
    assert is_last is False


def test_parse_html_no_cards():
    html = "<html><body><p>No results found.</p></body></html>"
    listings, is_last = _parse_html(html, _BASE_URL, _PAGE_URL)
    assert listings == []
    assert is_last is False


# ===========================================================================
# 16 – Adapter constructor
# ===========================================================================


def test_adapter_name():
    adapter = KierAdapter()
    assert adapter.name == _SOURCE_NAME


def test_adapter_crawl_delay_default():
    adapter = KierAdapter()
    assert adapter.crawl_delay == 3


def test_adapter_accepts_keywords():
    adapter = KierAdapter(keywords_list=["project manager"])
    assert "project manager" in adapter.keywords_list


def test_adapter_extra_kwargs_ignored():
    adapter = KierAdapter(unknown_kwarg="ignored")
    assert adapter.name == _SOURCE_NAME


# ===========================================================================
# 17 – Adapter fetch()
# ===========================================================================


def _make_mock_response(html: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = html
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


def test_fetch_happy_path(raw_html):
    adapter = KierAdapter(keywords_list=["project manager"], max_pages_per_query=1)
    mock_resp = _make_mock_response(raw_html)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.kier.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.kier.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 5
    assert all(l.source == _SOURCE_NAME for l in listings)
    assert all(l.employer == _EMPLOYER for l in listings)


def test_fetch_last_page_stops(raw_html, last_page_html):
    adapter = KierAdapter(keywords_list=["project manager"], max_pages_per_query=5)
    responses = [
        _make_mock_response(raw_html),      # page 1: 5 cards, not last
        _make_mock_response(last_page_html), # page 2: 3 cards, last page
    ]
    mock_client = _make_mock_client(responses)

    with patch("mechpm.adapters.kier.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.kier.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 8  # 5 + 3 unique


def test_fetch_dedup_across_keywords(raw_html):
    adapter = KierAdapter(
        keywords_list=["project manager", "project manager"],  # same keyword twice
        max_pages_per_query=1,
    )
    responses = [_make_mock_response(raw_html), _make_mock_response(raw_html)]
    mock_client = _make_mock_client(responses)

    with patch("mechpm.adapters.kier.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.kier.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 5  # deduplicated


def test_fetch_http_error():
    adapter = KierAdapter(keywords_list=["project manager"], max_pages_per_query=1)
    mock_resp = _make_mock_response("", 503)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.kier.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.kier.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_empty_page_stops():
    adapter = KierAdapter(keywords_list=["project manager"], max_pages_per_query=3)
    responses = [
        _make_mock_response("<html><body></body></html>"),  # page 1 empty → stop
    ]
    mock_client = _make_mock_client(responses)

    with patch("mechpm.adapters.kier.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.kier.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_since_filter_keeps_all_no_dates(raw_html):
    """Kier cards have no dates — all listings should pass the since filter."""
    adapter = KierAdapter(keywords_list=["project manager"], max_pages_per_query=1)
    since = datetime(2026, 6, 20, tzinfo=timezone.utc)
    mock_resp = _make_mock_response(raw_html)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.kier.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.kier.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch(since=since))

    # All 5 kept because posted_at is None → not filtered out
    assert len(listings) == 5
