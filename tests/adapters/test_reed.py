"""Unit tests for the Reed adapter, focusing on parameter construction.

Tests verify that:
1. When location="", locationName is NOT included in request params
2. When location="London", locationName=London IS included in request params
3. Default location is now "" to enable UK-wide (nationwide) searches
4. keywords_list multi-query support (M1): adapter iterates queries,
   unions results, and deduplicates by source_listing_id.

Date: 2026-06-15
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mechpm.adapters.reed import ReedAdapter


class TestReedLocationParam:
    """Regression tests for locationName parameter handling."""

    def test_empty_location_omits_locationname_param(self):
        """When location='', the locationName param must NOT be in the dict."""
        adapter = ReedAdapter(api_key="test-key", location="")
        params_dict = {
            "keywords": adapter.keywords,
            "contract": "true",
            "resultsToTake": adapter.results_to_take,
            "resultsToSkip": 0,
        }
        if adapter.location:
            params_dict["locationName"] = adapter.location

        assert "locationName" not in params_dict, (
            "locationName should not be present when location is empty"
        )

    def test_london_location_includes_locationname_param(self):
        """When location='London', the locationName param must be in the dict."""
        adapter = ReedAdapter(api_key="test-key", location="London")
        params_dict = {
            "keywords": adapter.keywords,
            "contract": "true",
            "resultsToTake": adapter.results_to_take,
            "resultsToSkip": 0,
        }
        if adapter.location:
            params_dict["locationName"] = adapter.location

        assert "locationName" in params_dict, (
            "locationName should be present when location is non-empty"
        )
        assert params_dict["locationName"] == "London", (
            f"Expected locationName='London', got {params_dict.get('locationName')!r}"
        )

    def test_default_location_is_empty(self):
        """The default location must be an empty string for UK-wide searches."""
        adapter = ReedAdapter(api_key="test-key")
        assert adapter.location == "", (
            f"Default location should be '', got {adapter.location!r}"
        )

    def test_custom_location_preserved(self):
        """Custom locations should be stored as-is."""
        test_locations = ["Manchester", "Bristol", "Edinburgh"]
        for loc in test_locations:
            adapter = ReedAdapter(api_key="test-key", location=loc)
            assert adapter.location == loc, (
                f"Expected location={loc!r}, got {adapter.location!r}"
            )

    def test_keywords_and_contract_always_present(self):
        """Verify that keywords and contract params are always included."""
        adapter = ReedAdapter(api_key="test-key", location="")
        params_dict = {
            "keywords": adapter.keywords,
            "contract": "true",
            "resultsToTake": adapter.results_to_take,
            "resultsToSkip": 0,
        }
        if adapter.location:
            params_dict["locationName"] = adapter.location

        assert "keywords" in params_dict, "keywords must be in params"
        assert "contract" in params_dict, "contract must be in params"
        assert params_dict["contract"] == "true", "contract must be 'true'"
        assert "resultsToTake" in params_dict, "resultsToTake must be in params"
        assert "resultsToSkip" in params_dict, "resultsToSkip must be in params"


# ---------------------------------------------------------------------------
# M1: keywords_list multi-query support
# ---------------------------------------------------------------------------

def _make_reed_response(jobs: list[dict]) -> MagicMock:
    """Helper: build a mock httpx response returning the given jobs list."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": jobs}
    resp.raise_for_status = MagicMock()
    return resp


def _make_job(job_id: int, title: str = "PM Test") -> dict:
    """Helper: minimal Reed API job dict."""
    return {
        "jobId": job_id,
        "jobTitle": title,
        "locationName": "London",
        "minimumSalary": None,
        "maximumSalary": None,
        "currency": "GBP",
        "salaryType": None,
        "date": None,
        "jobDescription": None,
        "jobUrl": f"https://www.reed.co.uk/jobs/{job_id}",
        "employerName": None,
    }


class TestReedMultiQuery:
    """Tests for keywords_list multi-query support (M1)."""

    def test_keywords_list_accepted(self):
        """ReedAdapter accepts keywords_list and stores it."""
        kw_list = ["project manager mechanical", "project manager HVAC"]
        adapter = ReedAdapter(api_key="test-key", keywords_list=kw_list)
        assert adapter.keywords_list == kw_list

    def test_keywords_scalar_wraps_into_list(self):
        """When only a scalar keywords is given, keywords_list is a single-item list."""
        adapter = ReedAdapter(api_key="test-key", keywords="project manager test")
        assert adapter.keywords_list == ["project manager test"]

    def test_default_keywords_list_single_item(self):
        """Default construction produces a single-item keywords_list."""
        adapter = ReedAdapter(api_key="test-key")
        assert len(adapter.keywords_list) == 1

    def test_multi_query_union_and_dedup(self):
        """Multi-query fetch unions results and deduplicates by source_listing_id.

        Query 1 returns jobs [1, 2]; query 2 returns jobs [2, 3].
        Unique result set must be {1, 2, 3} (job 2 deduplicated).
        """
        job1 = _make_job(1, "PM Mechanical")
        job2 = _make_job(2, "PM HVAC")
        job3 = _make_job(3, "Eng PM Contract")

        # Two queries → two page responses (each partial → breaks loop immediately).
        responses = iter([
            _make_reed_response([job1, job2]),   # keyword 1, page 1
            _make_reed_response([job2, job3]),   # keyword 2, page 1
        ])

        async def _run():
            adapter = ReedAdapter(
                api_key="test-key",
                keywords_list=["project manager mechanical", "project manager HVAC"],
                results_to_take=100,
                safety_cap=500,
            )
            with patch("mechpm.adapters.reed.asyncio.sleep", new=AsyncMock()):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client.get = AsyncMock(side_effect=lambda *a, **kw: next(responses))
                    mock_cls.return_value = mock_client
                    return await adapter.fetch()

        listings = asyncio.run(_run())
        assert len(listings) == 3, (
            f"Expected 3 unique listings (deduped), got {len(listings)}"
        )
        ids = {lst.source_listing_id for lst in listings}
        assert ids == {"1", "2", "3"}, f"Expected IDs {{1,2,3}}, got {ids!r}"

    def test_safety_cap_stops_additional_queries(self):
        """When safety_cap is reached after the first query, no further queries are made.

        safety_cap=1: after query1 returns 1 unique job, the cap is hit.
        The adapter must NOT fetch query2 at all (mock would raise StopIteration).
        """
        job1 = _make_job(10, "PM A")

        # Only ONE response available; a second GET would fail.
        responses = iter([_make_reed_response([job1])])

        async def _run():
            adapter = ReedAdapter(
                api_key="test-key",
                keywords_list=["kw1", "kw2"],
                results_to_take=100,
                safety_cap=1,
            )
            with patch("mechpm.adapters.reed.asyncio.sleep", new=AsyncMock()):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client.get = AsyncMock(side_effect=lambda *a, **kw: next(responses))
                    mock_cls.return_value = mock_client
                    return await adapter.fetch()

        listings = asyncio.run(_run())
        assert len(listings) == 1, (
            f"Expected 1 listing (safety_cap=1, one query fetched), got {len(listings)}"
        )
        assert listings[0].source_listing_id == "10"
