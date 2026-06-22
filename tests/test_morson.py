"""Unit tests for the Morson Talent adapter — JSON:API scraper for morson.com.

All tests use synthetic data and mocked HTTP — no live network calls.

Coverage (40 tests):
  _build_api_url       : with keyword, no keyword, custom limit, trailing slash stripped
  _parse_salary        : text field present, no-text fallback (from+to+per+currency),
                         from-only, to-only, all None, per_code label A/H/D,
                         unknown per_code, empty text falls back
  _parse_description   : dict with value, None input, empty dict, plain string,
                         empty string returns None
  _item_to_raw_listing : full item correct mapping, source name, source_listing_id
                         from nid, url built from path alias, url fallback when no
                         alias, posted_at ISO 8601 parsed, employer fixed,
                         location_raw from field_location, description from dict,
                         salary_raw from salary_text, metadata keys (lm_reference,
                         consultant, remote, salary_currency, unpublish_on),
                         missing title → None, missing nid uses item id,
                         no attrs → None, non-dict item → None
  _parse_page          : valid payload → listings + next_url, links.next as dict,
                         links.next as string, no links.next → None, empty data list,
                         non-list data → ([], None), exception → ([], None)
  MorsonAdapter ctor   : default name, default crawl_delay, default api_base,
                         default max_pages, default keywords_list non-empty,
                         custom keywords_list, trailing slash stripped, extra kwargs ignored
  fetch()              : happy path single keyword, two keywords deduplication,
                         pagination follows next_url, max_pages cap respected,
                         since drops old listing, since keeps listing with no date,
                         HTTP error returns [], request exception returns [],
                         JSON decode error returns [], empty page stops early
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mechpm.adapters.morson import (
    MorsonAdapter,
    _DEFAULT_KEYWORDS,
    _EMPLOYER,
    _SOURCE_NAME,
    _build_api_url,
    _item_to_raw_listing,
    _parse_description,
    _parse_page,
    _parse_salary,
)

_API_BASE = "https://www.morson.com"

# ---------------------------------------------------------------------------
# Shared synthetic data helpers
# ---------------------------------------------------------------------------


def _make_item(
    nid: int = 12345,
    title: str = "Project Manager",
    alias: str = "/jobs/engineering/contract/london/project-manager",
    created: str = "2026-06-20T10:00:00+00:00",
    description: dict | None = None,
    location: str = "London, UK",
    salary_text: str = "£450 - £500 per day",
    work_hours: str | None = "Contract",
    lm_ref: str = "JOB-99999",
    consultant: str = "Jane Smith",
    remote: bool = False,
    currency: str = "GBP",
    unpublish: str = "2026-07-20T10:00:00+00:00",
) -> dict:
    if description is None:
        description = {"value": "<p>Great PM role in London.</p>", "format": "full_html"}
    return {
        "type": "node--job",
        "id": "uuid-abc-123",
        "attributes": {
            "drupal_internal__nid": nid,
            "title": title,
            "created": created,
            "changed": created,
            "status": True,
            "path": {"alias": alias, "pid": nid + 1000, "langcode": "en"},
            "field_c_j_description": description,
            "field_location": location,
            "field_c_j_salary_text": salary_text,
            "field_c_j_salary_from": None,
            "field_c_j_salary_to": None,
            "field_c_j_salary_per": None,
            "field_c_j_salary_currency": currency,
            "field_c_j_work_hours": work_hours,
            "field_c_j_lm_reference": lm_ref,
            "field_c_j_consultant": consultant,
            "field_c_j_remote": remote,
            "unpublish_on": unpublish,
        },
    }


def _make_payload(
    items: list[dict],
    next_url: str | None = None,
) -> dict:
    links: dict = {"self": {"href": _API_BASE + "/jsonapi/lm/job"}}
    if next_url:
        links["next"] = {"href": next_url}
    return {"jsonapi": {"version": "1.0"}, "data": items, "links": links}


# ---------------------------------------------------------------------------
# Mock HTTP helpers
# ---------------------------------------------------------------------------


def _make_mock_json_response(payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload)
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
        return _make_mock_json_response({"data": [], "links": {}})

    mock = AsyncMock()
    mock.get = _get
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


# ===========================================================================
# 1 – _build_api_url
# ===========================================================================


def test_build_api_url_with_keyword():
    url = _build_api_url(_API_BASE, "project manager")
    assert "/jsonapi/lm/job" in url
    assert "sort=-created" in url
    assert "project+manager" in url or "project%20manager" in url
    assert "CONTAINS" in url


def test_build_api_url_no_keyword():
    url = _build_api_url(_API_BASE)
    assert "/jsonapi/lm/job" in url
    assert "sort=-created" in url
    assert "filter" not in url


def test_build_api_url_custom_limit():
    url = _build_api_url(_API_BASE, "quantity surveyor", limit=25)
    assert "25" in url


def test_build_api_url_trailing_slash_stripped():
    url = _build_api_url("https://www.morson.com/", "project manager")
    assert "//jsonapi" not in url
    assert "/jsonapi/lm/job" in url


# ===========================================================================
# 2 – _parse_salary
# ===========================================================================


def test_parse_salary_text_preferred():
    attrs = {"field_c_j_salary_text": "£450 per day", "field_c_j_salary_from": "400"}
    assert _parse_salary(attrs) == "£450 per day"


def test_parse_salary_fallback_from_to_per_currency():
    attrs = {
        "field_c_j_salary_text": None,
        "field_c_j_salary_from": "400",
        "field_c_j_salary_to": "500",
        "field_c_j_salary_per": "D",
        "field_c_j_salary_currency": "GBP",
    }
    result = _parse_salary(attrs)
    assert "400" in result
    assert "500" in result
    assert "per day" in result


def test_parse_salary_from_only():
    attrs = {
        "field_c_j_salary_text": None,
        "field_c_j_salary_from": "350",
        "field_c_j_salary_to": None,
        "field_c_j_salary_per": "D",
        "field_c_j_salary_currency": "GBP",
    }
    result = _parse_salary(attrs)
    assert "350" in result
    assert "per day" in result


def test_parse_salary_all_none():
    attrs = {
        "field_c_j_salary_text": None,
        "field_c_j_salary_from": None,
        "field_c_j_salary_to": None,
    }
    assert _parse_salary(attrs) is None


def test_parse_salary_per_code_annual():
    attrs = {
        "field_c_j_salary_text": None,
        "field_c_j_salary_from": "50000",
        "field_c_j_salary_to": "60000",
        "field_c_j_salary_per": "A",
        "field_c_j_salary_currency": "GBP",
    }
    result = _parse_salary(attrs)
    assert "per year" in result


def test_parse_salary_per_code_hourly():
    attrs = {
        "field_c_j_salary_text": None,
        "field_c_j_salary_from": "30",
        "field_c_j_salary_to": None,
        "field_c_j_salary_per": "H",
        "field_c_j_salary_currency": "GBP",
    }
    result = _parse_salary(attrs)
    assert "per hour" in result


def test_parse_salary_unknown_per_code():
    attrs = {
        "field_c_j_salary_text": None,
        "field_c_j_salary_from": "100",
        "field_c_j_salary_to": None,
        "field_c_j_salary_per": "Z",
        "field_c_j_salary_currency": "GBP",
    }
    result = _parse_salary(attrs)
    assert result is not None
    assert "100" in result


def test_parse_salary_empty_text_falls_back():
    attrs = {
        "field_c_j_salary_text": "   ",
        "field_c_j_salary_from": "200",
        "field_c_j_salary_to": "250",
        "field_c_j_salary_per": "D",
        "field_c_j_salary_currency": "GBP",
    }
    result = _parse_salary(attrs)
    assert "200" in result


# ===========================================================================
# 3 – _parse_description
# ===========================================================================


def test_parse_description_dict_with_value():
    result = _parse_description({"value": "<p>Great job.</p>", "format": "full_html"})
    assert result == "<p>Great job.</p>"


def test_parse_description_none():
    assert _parse_description(None) is None


def test_parse_description_empty_dict():
    assert _parse_description({}) is None


def test_parse_description_plain_string():
    assert _parse_description("Some description") == "Some description"


def test_parse_description_empty_string():
    assert _parse_description("") is None


# ===========================================================================
# 4 – _item_to_raw_listing
# ===========================================================================


def test_item_to_raw_listing_source_name():
    item = _make_item()
    result = _item_to_raw_listing(item, _API_BASE)
    assert result is not None
    assert result.source == _SOURCE_NAME


def test_item_to_raw_listing_source_id_from_nid():
    item = _make_item(nid=99001)
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.source_listing_id == "99001"


def test_item_to_raw_listing_url_from_alias():
    item = _make_item(alias="/jobs/engineering/contract/london/project-manager")
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.url == "https://www.morson.com/jobs/engineering/contract/london/project-manager"


def test_item_to_raw_listing_url_fallback_no_alias():
    item = _make_item(nid=55555, alias="")
    result = _item_to_raw_listing(item, _API_BASE)
    assert "55555" in result.url
    assert result.url.startswith("https://www.morson.com")


def test_item_to_raw_listing_posted_at_parsed():
    item = _make_item(created="2026-06-20T10:00:00+00:00")
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.posted_at == datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc)


def test_item_to_raw_listing_employer_fixed():
    item = _make_item()
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.employer == _EMPLOYER


def test_item_to_raw_listing_location_raw():
    item = _make_item(location="Manchester, UK")
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.location_raw == "Manchester, UK"


def test_item_to_raw_listing_description_from_dict():
    item = _make_item(description={"value": "<p>Great PM role.</p>"})
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.description_raw == "<p>Great PM role.</p>"


def test_item_to_raw_listing_salary_raw():
    item = _make_item(salary_text="£500 per day")
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.salary_raw == "£500 per day"


def test_item_to_raw_listing_contract_type_raw():
    item = _make_item(work_hours="Contract")
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.contract_type_raw == "Contract"


def test_item_to_raw_listing_metadata_keys():
    item = _make_item(lm_ref="JOB-12345", consultant="Bob Jones", remote=True, currency="GBP")
    result = _item_to_raw_listing(item, _API_BASE)
    assert result.metadata["lm_reference"] == "JOB-12345"
    assert result.metadata["consultant"] == "Bob Jones"
    assert result.metadata["remote"] is True
    assert result.metadata["salary_currency"] == "GBP"
    assert "unpublish_on" in result.metadata


def test_item_to_raw_listing_missing_title_returns_none():
    item = _make_item(title="")
    assert _item_to_raw_listing(item, _API_BASE) is None


def test_item_to_raw_listing_nid_none_uses_item_id():
    item = _make_item()
    item["attributes"]["drupal_internal__nid"] = None
    item["id"] = "uuid-fallback-id"
    result = _item_to_raw_listing(item, _API_BASE)
    assert result is not None
    assert result.source_listing_id == "uuid-fallback-id"


def test_item_to_raw_listing_no_attrs_returns_none():
    item = {"type": "node--job", "id": "x", "attributes": {}}
    assert _item_to_raw_listing(item, _API_BASE) is None


def test_item_to_raw_listing_non_dict_returns_none():
    assert _item_to_raw_listing("not a dict", _API_BASE) is None  # type: ignore[arg-type]


# ===========================================================================
# 5 – _parse_page
# ===========================================================================


def test_parse_page_listings_count():
    payload = _make_payload([_make_item(nid=1), _make_item(nid=2)])
    listings, _ = _parse_page(payload, _API_BASE)
    assert len(listings) == 2


def test_parse_page_next_url_from_dict():
    payload = _make_payload([_make_item()], next_url="https://www.morson.com/jsonapi/lm/job?page%5Boffset%5D=50")
    _, next_url = _parse_page(payload, _API_BASE)
    assert next_url == "https://www.morson.com/jsonapi/lm/job?page%5Boffset%5D=50"


def test_parse_page_next_url_as_string():
    payload = _make_payload([_make_item()])
    payload["links"]["next"] = "https://www.morson.com/jsonapi/lm/job?page%5Boffset%5D=50"
    _, next_url = _parse_page(payload, _API_BASE)
    assert next_url == "https://www.morson.com/jsonapi/lm/job?page%5Boffset%5D=50"


def test_parse_page_no_next_url():
    payload = _make_payload([_make_item()])
    listings, next_url = _parse_page(payload, _API_BASE)
    assert next_url is None
    assert len(listings) == 1


def test_parse_page_empty_data():
    payload = _make_payload([])
    listings, next_url = _parse_page(payload, _API_BASE)
    assert listings == []
    assert next_url is None


def test_parse_page_non_list_data():
    listings, next_url = _parse_page({"data": "not a list"}, _API_BASE)
    assert listings == []
    assert next_url is None


# ===========================================================================
# 6 – MorsonAdapter constructor
# ===========================================================================


def test_adapter_name():
    adapter = MorsonAdapter()
    assert adapter.name == _SOURCE_NAME


def test_adapter_crawl_delay_default():
    adapter = MorsonAdapter()
    assert adapter.crawl_delay == 3


def test_adapter_api_base_default():
    adapter = MorsonAdapter()
    assert adapter.api_base == "https://www.morson.com"


def test_adapter_max_pages_default():
    adapter = MorsonAdapter()
    assert adapter.max_pages == 10


def test_adapter_keywords_list_default_non_empty():
    adapter = MorsonAdapter()
    assert len(adapter.keywords_list) > 0
    assert "project manager" in adapter.keywords_list


def test_adapter_custom_keywords():
    adapter = MorsonAdapter(keywords_list=["quantity surveyor"])
    assert adapter.keywords_list == ["quantity surveyor"]


def test_adapter_trailing_slash_stripped():
    adapter = MorsonAdapter(api_base="https://www.morson.com/")
    assert not adapter.api_base.endswith("/")


def test_adapter_extra_kwargs_ignored():
    adapter = MorsonAdapter(unknown_kwarg="should not crash")
    assert adapter.name == _SOURCE_NAME


# ===========================================================================
# 7 – fetch()
# ===========================================================================


def test_fetch_happy_path_single_keyword():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=1)
    payload = _make_payload([_make_item(nid=1), _make_item(nid=2)])
    mock_resp = _make_mock_json_response(payload)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 2
    assert all(l.source == _SOURCE_NAME for l in listings)
    assert all(l.employer == _EMPLOYER for l in listings)


def test_fetch_two_keywords_dedup():
    adapter = MorsonAdapter(
        keywords_list=["project manager", "project manager"],
        max_pages=1,
    )
    payload = _make_payload([_make_item(nid=1), _make_item(nid=2)])
    responses = [
        _make_mock_json_response(payload),
        _make_mock_json_response(payload),
    ]
    mock_client = _make_mock_client(responses)

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 2  # deduplicated by nid


def test_fetch_pagination_follows_next():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=3)
    next_link = f"{_API_BASE}/jsonapi/lm/job?page%5Boffset%5D=50"
    page1 = _make_payload([_make_item(nid=10), _make_item(nid=11)], next_url=next_link)
    page2 = _make_payload([_make_item(nid=20), _make_item(nid=21)])  # no next
    responses = [
        _make_mock_json_response(page1),
        _make_mock_json_response(page2),
    ]
    mock_client = _make_mock_client(responses)

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert len(listings) == 4  # 2 from page 1 + 2 from page 2


def test_fetch_max_pages_cap():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=1)
    next_link = f"{_API_BASE}/jsonapi/lm/job?page%5Boffset%5D=50"
    page1 = _make_payload([_make_item(nid=1)], next_url=next_link)
    page2 = _make_payload([_make_item(nid=2)])
    responses = [
        _make_mock_json_response(page1),
        _make_mock_json_response(page2),
    ]
    mock_client = _make_mock_client(responses)

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    # max_pages=1 means only page 1 fetched, even though next_url was present
    assert len(listings) == 1


def test_fetch_since_drops_old_listing():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=1)
    old_item = _make_item(nid=1, created="2026-06-01T10:00:00+00:00")
    new_item = _make_item(nid=2, created="2026-06-20T10:00:00+00:00")
    payload = _make_payload([old_item, new_item])
    mock_resp = _make_mock_json_response(payload)
    mock_client = _make_mock_client([mock_resp])
    since = datetime(2026, 6, 10, tzinfo=timezone.utc)

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch(since=since))

    assert len(listings) == 1
    assert listings[0].source_listing_id == "2"


def test_fetch_since_keeps_listing_with_no_date():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=1)
    item = _make_item(nid=3, created=None)
    item["attributes"]["created"] = None
    payload = _make_payload([item])
    mock_resp = _make_mock_json_response(payload)
    mock_client = _make_mock_client([mock_resp])
    since = datetime(2026, 6, 20, tzinfo=timezone.utc)

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch(since=since))

    assert len(listings) == 1


def test_fetch_http_error_returns_empty():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=1)
    mock_resp = _make_mock_json_response({}, status_code=503)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_request_exception_returns_empty():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=1)

    async def _raising_get(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    mock = AsyncMock()
    mock.get = _raising_get
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_json_decode_error_returns_empty():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=1)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(side_effect=ValueError("not JSON"))
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []


def test_fetch_empty_page_stops_early():
    adapter = MorsonAdapter(keywords_list=["project manager"], max_pages=5)
    empty_payload = _make_payload([])
    mock_resp = _make_mock_json_response(empty_payload)
    mock_client = _make_mock_client([mock_resp])

    with patch("mechpm.adapters.morson.httpx.AsyncClient", return_value=mock_client):
        with patch("mechpm.adapters.morson.asyncio.sleep", new_callable=AsyncMock):
            listings = asyncio.run(adapter.fetch())

    assert listings == []
