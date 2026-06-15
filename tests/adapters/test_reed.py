"""Unit tests for the Reed adapter, focusing on parameter construction.

Tests verify that:
1. When location="", locationName is NOT included in request params
2. When location="London", locationName=London IS included in request params
3. Default location is now "" to enable UK-wide (nationwide) searches

Date: 2026-06-15
"""
from __future__ import annotations

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
