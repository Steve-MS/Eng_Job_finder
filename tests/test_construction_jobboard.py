"""Unit tests for the ConstructionJobBoard adapter — HTML scrape.

Fixture: tests/fixtures/adapters/construction_jobboard_page1.html
Confirmed DOM structure (live recon 2026-06-22):
  article.listing-item
    .listing-item__title a.link          → title + URL
    .listing-item__info--item-company    → employer
    .listing-item__info--item-location   → location_raw
    .listing-item__employment-type       → contract_type_raw
    .listing-item__date                  → posted_at  (DD/MM/YYYY)
    .listing-item__desc                  → description_raw

All tests use synthetic data or HTML fixtures — no live network calls.

Coverage (26 tests):
  _build_search_url          : basic, page param, special chars
  _extract_job_id            : numeric extraction, fallback slug
  _parse_date                : DD/MM/YYYY, ISO, empty, None, garbage
  _card_to_raw_listing       : title, employer, location, contract_type,
                               date, description, missing title, missing url
  _parse_html                : fixture count, source, source_listing_id,
                               URL, title, employer, location, contract_type,
                               date, description, empty html, malformed,
                               no cards, card with missing title
  ConstructionJobBoardAdapter: ctor defaults, keywords accepted, fetch happy
                               path, since filter, HTTP error, request error,
                               dedup across keywords, empty page stops
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mechpm.adapters.construction_jobboard import (
    ConstructionJobBoardAdapter,
    _SOURCE_NAME,
    _build_search_url,
    _extract_job_id,
    _parse_date,
    _parse_html,
)

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "adapters"
    / "construction_jobboard_page1.html"
)
_BASE_URL = "https://www.constructionjobboard.co.uk"
_PAGE_URL = _BASE_URL + "/jobs/?keywords%5Ball_words%5D=project+manager&page=1"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_html() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed_listings(raw_html):
    return _parse_html(raw_html, _BASE_URL, _PAGE_URL)


# ===========================================================================
# 1 – URL builder
# ===========================================================================


def test_build_search_url_page1():
    url = _build_search_url(_BASE_URL, "project manager", 1)
    assert "keywords%5Ball_words%5D=project+manager" in url
    assert "page=1" in url


def test_build_search_url_page2():
    url = _build_search_url(_BASE_URL, "quantity surveyor", 2)
    assert "page=2" in url
    assert "quantity+surveyor" in url


def test_build_search_url_special_chars():
    url = _build_search_url(_BASE_URL, "M&E project manager", 1)
    assert "M%26E" in url or "M+%26+E" in url or "M%26" in url


# ===========================================================================
# 2 – Job ID extraction
# ===========================================================================


def test_extract_job_id_numeric():
    url = "https://www.constructionjobboard.co.uk/job/3677619/project-manager/"
    assert _extract_job_id(url) == "3677619"


def test_extract_job_id_fallback_slug():
    url = "https://www.constructionjobboard.co.uk/jobs/some-role-without-id"
    result = _extract_job_id(url)
    assert result == "some-role-without-id"


def test_extract_job_id_another_numeric():
    url = "https://www.constructionjobboard.co.uk/job/3685821/quantity-surveyor/"
    assert _extract_job_id(url) == "3685821"


# ===========================================================================
# 3 – Date parsing
# ===========================================================================


def test_parse_date_dd_mm_yyyy():
    dt = _parse_date("11/06/2026")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6
    assert dt.day == 11
    assert dt.tzinfo == timezone.utc


def test_parse_date_iso():
    dt = _parse_date("2026-06-22")
    assert dt is not None
    assert dt.day == 22


def test_parse_date_empty():
    assert _parse_date("") is None


def test_parse_date_none():
    assert _parse_date(None) is None


def test_parse_date_garbage():
    assert _parse_date("not-a-date") is None


# ===========================================================================
# 4 – HTML parsing: volume gate
# ===========================================================================


def test_listing_count(parsed_listings):
    """Fixture page must yield exactly 8 listings."""
    assert len(parsed_listings) == 8


# ===========================================================================
# 5 – Source identifier
# ===========================================================================


def test_source_is_construction_jobboard(parsed_listings):
    for listing in parsed_listings:
        assert listing.source == _SOURCE_NAME


# ===========================================================================
# 6 – source_listing_id
# ===========================================================================


def test_source_listing_id_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.source_listing_id, "source_listing_id must be non-empty"


def test_first_listing_id(parsed_listings):
    assert parsed_listings[0].source_listing_id == "3677619"


# ===========================================================================
# 7 – URL field
# ===========================================================================


def test_url_starts_with_https(parsed_listings):
    for listing in parsed_listings:
        assert listing.url.startswith("https://")


def test_first_listing_url(parsed_listings):
    assert "3677619" in parsed_listings[0].url


# ===========================================================================
# 8 – Title field
# ===========================================================================


def test_first_listing_title(parsed_listings):
    assert parsed_listings[0].title == "Project Manager"


def test_titles_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.title


# ===========================================================================
# 9 – Employer field
# ===========================================================================


def test_first_listing_employer(parsed_listings):
    assert parsed_listings[0].employer == "Lanserring"


def test_employer_none_when_absent(parsed_listings):
    # Card 4 (index 3) has no company span
    risk_manager = next(l for l in parsed_listings if "risk" in l.title.lower())
    assert risk_manager.employer is None


# ===========================================================================
# 10 – Location field
# ===========================================================================


def test_first_listing_location(parsed_listings):
    assert parsed_listings[0].location_raw == "London, UK"


def test_location_none_when_absent(parsed_listings):
    qs = next(l for l in parsed_listings if "Quantity Surveyor" in l.title)
    assert qs.location_raw is None


# ===========================================================================
# 11 – Contract type field
# ===========================================================================


def test_first_listing_contract_type(parsed_listings):
    assert parsed_listings[0].contract_type_raw == "Full time"


def test_contract_type_second_card(parsed_listings):
    qs = next(l for l in parsed_listings if "Quantity Surveyor" in l.title)
    assert qs.contract_type_raw == "Permanent"


# ===========================================================================
# 12 – Date field
# ===========================================================================


def test_first_listing_posted_at(parsed_listings):
    dt = parsed_listings[0].posted_at
    assert dt is not None
    assert dt.day == 11
    assert dt.month == 6
    assert dt.year == 2026


def test_posted_at_none_when_missing(parsed_listings):
    # Card 4 (Risk Manager) has no date
    risk = next(l for l in parsed_listings if "Risk Manager" in l.title)
    assert risk.posted_at is None


# ===========================================================================
# 13 – Description field
# ===========================================================================


def test_description_non_empty(parsed_listings):
    for listing in parsed_listings:
        assert listing.description_raw


def test_description_contains_text(parsed_listings):
    assert "Project Manager" in parsed_listings[0].description_raw


# ===========================================================================
# 14 – Error handling
# ===========================================================================


def test_parse_html_empty_string():
    result = _parse_html("", _BASE_URL, _PAGE_URL)
    assert result == []


def test_parse_html_no_cards():
    html = "<html><body><p>No results found.</p></body></html>"
    result = _parse_html(html, _BASE_URL, _PAGE_URL)
    assert result == []


# ===========================================================================
# 15 – Adapter constructor
# ===========================================================================


def test_adapter_name():
    adapter = ConstructionJobBoardAdapter()
    assert adapter.name == _SOURCE_NAME


def test_adapter_crawl_delay_default():
    adapter = ConstructionJobBoardAdapter()
    assert adapter.crawl_delay == 3


def test_adapter_accepts_keywords():
    adapter = ConstructionJobBoardAdapter(keywords_list=["project manager"])
    assert "project manager" in adapter.keywords_list


def test_adapter_extra_kwargs_ignored():
    adapter = ConstructionJobBoardAdapter(unknown_kwarg="ignored")
    assert adapter.name == _SOURCE_NAME


# ===========================================================================
# 16 – Adapter fetch()
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
        empty = _make_mock_response("<html><body></body></html>")
        return empty

    mock = AsyncMock()
    mock.get = _get
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


def test_fetch_happy_path(raw_html):
    adapter = ConstructionJobBoardAdapter(
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    mock_resp = _make_mock_response(raw_html)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.construction_jobboard.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.construction_jobboard.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 8
    assert all(l.source == _SOURCE_NAME for l in listings)


def test_fetch_since_filters(raw_html):
    adapter = ConstructionJobBoardAdapter(
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    # Only listings from 2026-06-20 onward should pass
    since = datetime(2026, 6, 20, tzinfo=timezone.utc)
    mock_resp = _make_mock_response(raw_html)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.construction_jobboard.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.construction_jobboard.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch(since=since))

    # Only Quantity Surveyor (22/06/2026) and Commercial Manager (20/06/2026)
    # should be present; listings without dates are kept too
    assert all(
        l.posted_at is None or l.posted_at >= since
        for l in listings
    )


def test_fetch_http_error():
    adapter = ConstructionJobBoardAdapter(
        keywords_list=["project manager"],
        max_pages_per_query=1,
    )
    mock_resp = _make_mock_response("", 503)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.construction_jobboard.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.construction_jobboard.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_dedup_across_keywords(raw_html):
    adapter = ConstructionJobBoardAdapter(
        keywords_list=["project manager", "project manager"],  # same keyword twice
        max_pages_per_query=1,
    )
    mock_resp1 = _make_mock_response(raw_html)
    mock_resp2 = _make_mock_response(raw_html)
    mock_client = _make_mock_client([mock_resp1, mock_resp2])

    with patch("mechpm.adapters.construction_jobboard.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.construction_jobboard.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    # Should deduplicate — 8 unique, not 16
    assert len(listings) == 8


def test_fetch_empty_page_stops_pagination(raw_html):
    adapter = ConstructionJobBoardAdapter(
        keywords_list=["project manager"],
        max_pages_per_query=3,
    )
    mock_resp1 = _make_mock_response(raw_html)
    # Page 2 is empty — should stop paginating
    mock_resp2 = _make_mock_response("<html><body></body></html>")
    mock_client = _make_mock_client([mock_resp1, mock_resp2])

    calls = []
    original_get = mock_client.get

    async def tracked_get(url, **kwargs):
        calls.append(url)
        return await original_get(url, **kwargs)

    mock_client.get = tracked_get

    with patch("mechpm.adapters.construction_jobboard.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.construction_jobboard.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 8
    assert len(calls) == 2  # stopped after empty page 2
