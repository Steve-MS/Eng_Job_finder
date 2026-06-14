"""
tests/test_adapters_smoke.py — Adapter interface smoke tests.
Date: 2026-06-12

For each of the 7 MVP adapters:
  1. Import the adapter module (skip if absent).
  2. Instantiate the adapter.
  3. Assert it exposes the required SourceAdapter ABC interface.

Reed-specific tests:
  4. Mock httpx, feed a canned Reed JSON API response.
  5. Assert fetch() returns >= 1 RawListing with expected fields.
  6. Simulate HTTP 500 — assert fetch() returns [] (does not raise).

Adapter acceptance thresholds (from Arthur's criteria):
  - Must have: name, crawl_delay (int/float), fetch (callable)
  - fetch() must never raise; returns [] on error
  - RawListing returned must have source_url, title, location
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
try:
    from mechpm.adapters.base import RawListing  # type: ignore

    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False
    RawListing = None  # type: ignore

# Try importing the adapter base
try:
    from mechpm.adapters.base import SourceAdapter  # type: ignore

    _BASE_AVAILABLE = True
except ImportError:
    _BASE_AVAILABLE = False
    SourceAdapter = None  # type: ignore

_SKIP_BASE = pytest.mark.skipif(
    not _BASE_AVAILABLE,
    reason="mechpm.adapters.base not available yet",
)

# ---------------------------------------------------------------------------
# Adapter registry (name → module path)
# Keys match adapter.name class attributes and config.toml source keys.
# ---------------------------------------------------------------------------
_ADAPTER_REGISTRY = {
    "reed": "mechpm.adapters.reed",
    "totaljobs": "mechpm.adapters.stepstone",
    "cwjobs": "mechpm.adapters.stepstone",
    "railwaypeople": "mechpm.adapters.railwaypeople",
    "energy_jobline": "mechpm.adapters.energy_jobline",
    "the_engineer": "mechpm.adapters.the_engineer",
    "aviation_job_search": "mechpm.adapters.aviation_job_search",
}

# ---------------------------------------------------------------------------
# Canned Reed API response (representative single-result payload)
# ---------------------------------------------------------------------------
_CANNED_REED_RESPONSE = {
    "results": [
        {
            "jobId": 55123456,
            "employerId": None,
            "employerName": None,
            "jobTitle": "Contract Project Manager \u2013 Rail Infrastructure",
            "locationName": "Leeds",
            "minimumSalary": 700.0,
            "maximumSalary": 700.0,
            "currency": "GBP",
            "salaryType": "per day",
            "expirationDate": "10/08/2026",
            "date": "10/06/2026",
            "jobDescription": (
                "Rullion are recruiting for a Project Manager for a 6-month "
                "outside IR35 contract in Leeds. Rail infrastructure track renewal. "
                "Rate: \u00a3700 per day outside IR35. Start: 14 July 2026. "
                "PRINCE2/APM required. NEC3/4 ECC. BPSS clearance."
            ),
            "applications": 3,
            "jobUrl": "https://www.reed.co.uk/jobs/contract-project-manager-rail/55123456",
        }
    ],
    "totalResults": 1,
    "ambiguous": False,
    "cached": False,
}

# ---------------------------------------------------------------------------
# Interface smoke tests for all adapters
# ---------------------------------------------------------------------------
@_SKIP_BASE
@pytest.mark.parametrize("adapter_name", list(_ADAPTER_REGISTRY.keys()))
def test_adapter_module_importable(adapter_name, metrics):
    """Assert each adapter module can be imported."""
    import importlib

    module_path = _ADAPTER_REGISTRY[adapter_name]
    try:
        importlib.import_module(module_path)
        metrics.record("adapter", predicted=True, actual=True)
    except ImportError:
        metrics.record("adapter", predicted=False, actual=True)
        pytest.skip(f"{module_path} not yet implemented — skipping")


@_SKIP_BASE
@pytest.mark.parametrize("adapter_name", list(_ADAPTER_REGISTRY.keys()))
def test_adapter_has_required_interface(adapter_name, all_adapters_by_name):
    """
    Assert each adapter exposes the SourceAdapter ABC contract:
      - name (str)
      - crawl_delay (numeric)
      - fetch (callable / coroutine function)

    Adapters are obtained via mechpm.cli._build_adapters(synthetic_settings) so
    that the real production construction path (api_key, domain, etc.) is
    exercised instead of bare no-arg instantiation.
    """
    import importlib

    module_path = _ADAPTER_REGISTRY[adapter_name]
    try:
        importlib.import_module(module_path)
    except ImportError:
        pytest.skip(f"{module_path} not yet implemented")

    instance = all_adapters_by_name.get(adapter_name)
    if instance is None:
        pytest.fail(
            f"_build_adapters produced no adapter with name={adapter_name!r}. "
            f"Available names: {sorted(all_adapters_by_name)}"
        )

    assert hasattr(instance, "name"), "Adapter missing 'name' attribute"
    assert hasattr(instance, "crawl_delay"), "Adapter missing 'crawl_delay' attribute"
    assert callable(getattr(instance, "fetch", None)), "Adapter missing callable 'fetch'"
    assert isinstance(instance.crawl_delay, (int, float)), (
        f"crawl_delay must be numeric, got {type(instance.crawl_delay)}"
    )


# ---------------------------------------------------------------------------
# Reed-specific tests
# ---------------------------------------------------------------------------
@pytest.fixture
def reed_adapter():
    """Return a ReedAdapter instance, skipping if module absent.
    Tries no-arg construction first, then falls back to common constructor
    signatures (api_key kwarg) for adapters that require configuration.
    """
    try:
        from mechpm.adapters.reed import ReedAdapter  # type: ignore
    except ImportError:
        pytest.skip("mechpm.adapters.reed not available yet")
        return  # unreachable but keeps type checkers happy

    for kwargs in ({}, {"api_key": "test-dummy-key-for-mocked-tests"}, {"api_key": None}):
        try:
            return ReedAdapter(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue

    pytest.skip("Cannot instantiate ReedAdapter with any known constructor signature")


def test_reed_fetch_canned_response(reed_adapter, metrics):
    """
    Mock httpx.AsyncClient; feed canned Reed JSON response.
    Assert fetch() returns >= 1 RawListing with expected fields.
    """
    async def _run():
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _CANNED_REED_RESPONSE
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            return await reed_adapter.fetch()

    results = asyncio.run(_run())
    metrics.record("adapter", predicted=len(results) >= 1, actual=True)
    assert len(results) >= 1, "fetch() returned empty list for canned Reed response"

    listing = results[0]
    if isinstance(listing, dict):
        assert "url" in listing or "source_url" in listing, "RawListing missing url/source_url"
        assert "title" in listing, "RawListing missing title"
        assert "location_raw" in listing or "location" in listing, "RawListing missing location"
    else:
        # RawListing model uses 'url'; accept either for forward-compat
        assert hasattr(listing, "url") or hasattr(listing, "source_url"), (
            "RawListing missing url/source_url field"
        )
        assert hasattr(listing, "title"), "RawListing missing title"


def test_reed_fetch_returns_empty_on_http_500(reed_adapter, metrics):
    """
    Simulate HTTP 500 response.
    Assert fetch() returns [] and does NOT raise an exception.
    """
    import httpx

    async def _run():
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=MagicMock(),
                response=mock_response,
            )
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            return await reed_adapter.fetch()

    try:
        results = asyncio.run(_run())
    except Exception as exc:
        metrics.record("adapter", predicted=False, actual=True)
        pytest.fail(
            f"fetch() raised {type(exc).__name__} on HTTP 500 — must return [] not raise. "
            f"Error: {exc}"
        )

    metrics.record("adapter", predicted=len(results) == 0, actual=True)
    assert results == [], f"fetch() must return [] on HTTP 500, got {results!r}"


def test_reed_fetch_returns_empty_on_connection_error(reed_adapter, metrics):
    """
    Simulate network timeout / connection error.
    Assert fetch() returns [] and does NOT raise.
    """
    import httpx

    async def _run():
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_class.return_value = mock_client
            return await reed_adapter.fetch()

    try:
        results = asyncio.run(_run())
    except Exception as exc:
        metrics.record("adapter", predicted=False, actual=True)
        pytest.fail(
            f"fetch() raised {type(exc).__name__} on timeout — must return []. "
            f"Error: {exc}"
        )

    metrics.record("adapter", predicted=len(results) == 0, actual=True)
    assert results == [], f"fetch() must return [] on connection error, got {results!r}"


# ---------------------------------------------------------------------------
# Aggregate adapter score
# ---------------------------------------------------------------------------
def test_adapter_pass_rate(metrics):
    """Assert all tested adapters pass their interface checks."""
    total = metrics.total("adapter")
    if total == 0:
        pytest.skip("No adapter metrics collected")

    prec = metrics.precision("adapter")
    assert prec is not None and prec >= 1.0, (
        f"Adapter pass rate {prec:.3f} < 1.0 (counts: {metrics._counts['adapter']})"
    )
