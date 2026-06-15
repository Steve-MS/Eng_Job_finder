"""
tests/conftest.py — Shared pytest fixtures and session hooks for mech-pm-finder.
Date: 2026-06-12

Adds src/ to sys.path so ``import mechpm`` works without an editable install.
All src/mechpm imports are wrapped in try/except so test collection never crashes
when the implementation modules are not yet present.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — must run before any mechpm import attempt
# ---------------------------------------------------------------------------
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ---------------------------------------------------------------------------
# Lazy model imports — gracefully absent until Michael/Ada deliver src/mechpm/
# ---------------------------------------------------------------------------
try:
    from mechpm.adapters.base import RawListing  # type: ignore
    from mechpm.models import NormalizedListing  # type: ignore

    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False
    RawListing = None  # type: ignore
    NormalizedListing = None  # type: ignore

# ---------------------------------------------------------------------------
# Session-wide metrics collector
# Tests call ``metrics.record(dimension, predicted, actual)`` to accumulate
# TP/FP/TN/FN counts.  The pytest_sessionfinish hook writes a JSON report
# that acceptance_gate.py reads to check threshold compliance.
# ---------------------------------------------------------------------------
_METRICS_OUTPUT = Path(__file__).parent / ".test_metrics.json"

FILTER_DIMENSIONS = [
    "contract_filter",
    "uk_filter",
    "pm_filter",
    "mech_filter",
    "dedup_merge",
    "extraction",
    "adapter",
]


class MetricsCollector:
    """Accumulates confusion-matrix counts across the test session."""

    def __init__(self) -> None:
        self._counts: dict[str, dict[str, int]] = {
            d: {"tp": 0, "fp": 0, "tn": 0, "fn": 0} for d in FILTER_DIMENSIONS
        }

    def record(self, dimension: str, *, predicted: bool, actual: bool) -> None:
        """Record one classification decision for a named dimension."""
        d = self._counts.setdefault(dimension, {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
        if predicted and actual:
            d["tp"] += 1
        elif predicted and not actual:
            d["fp"] += 1
        elif not predicted and actual:
            d["fn"] += 1
        else:
            d["tn"] += 1

    def precision(self, dimension: str) -> float | None:
        d = self._counts.get(dimension, {})
        tp, fp = d.get("tp", 0), d.get("fp", 0)
        return tp / (tp + fp) if (tp + fp) > 0 else None

    def recall(self, dimension: str) -> float | None:
        d = self._counts.get(dimension, {})
        tp, fn = d.get("tp", 0), d.get("fn", 0)
        return tp / (tp + fn) if (tp + fn) > 0 else None

    def total(self, dimension: str) -> int:
        d = self._counts.get(dimension, {})
        return sum(d.values())

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for dim in FILTER_DIMENSIONS:
            d = self._counts[dim]
            tp, fp, tn, fn = d["tp"], d["fp"], d["tn"], d["fn"]
            total = tp + fp + tn + fn
            prec = tp / (tp + fp) if (tp + fp) > 0 else None
            rec = tp / (tp + fn) if (tp + fn) > 0 else None
            result[dim] = {
                "counts": dict(d),
                "total": total,
                "precision": prec,
                "recall": rec,
            }
        return result


_session_metrics = MetricsCollector()


@pytest.fixture(scope="session")
def metrics() -> MetricsCollector:
    """Session-scoped metrics collector shared across all test modules."""
    return _session_metrics


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write aggregated metrics to JSON after all tests complete."""
    try:
        _METRICS_OUTPUT.write_text(
            json.dumps(_session_metrics.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass  # Never crash pytest teardown


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db():
    """In-memory SQLite connection with row_factory = sqlite3.Row."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture()
def mock_openai_client():
    """
    MagicMock that mimics openai.OpenAI, returning canned extraction payloads.
    Used to exercise the extractor's LLM fallback path without real API calls.
    The canned response matches a typical outside-IR35 mechanical PM role.
    """
    client = MagicMock()
    canned_payload = {
        "day_rate_min": 600.0,
        "day_rate_max": 650.0,
        "ir35_status": "outside",
        "duration_weeks": 26,
        "start_date": "2026-08-01",
        "location": "Manchester",
        "remote_policy": "onsite",
        "seniority": "senior",
        "sectors": ["mechanical engineering"],
        "is_uk": True,
        "is_mech_eng": True,
        "is_pm_role": True,
        "is_contract": True,
        "asap_flag": False,
    }
    canned = MagicMock()
    canned.choices = [MagicMock()]
    canned.choices[0].message.content = json.dumps(canned_payload)
    client.chat.completions.create.return_value = canned
    # Support async usage pattern too
    client.chat.completions.acreate = AsyncMock(return_value=canned)
    return client


@pytest.fixture()
def sample_raw_listing() -> Callable[..., Any]:
    """
    Factory fixture that returns a callable producing RawListing-like dicts
    (or model instances when mechpm.models is available).
    Keyword args override any default field.

    Usage::

        def test_something(sample_raw_listing):
            raw = sample_raw_listing(title="Senior PM – Rail")
    """

    def _factory(**overrides: Any) -> Any:
        base: dict[str, Any] = {
            "source": "reed",
            "url": "https://www.reed.co.uk/jobs/contract-project-manager/99000001",
            "source_listing_id": "99000001",
            "title": "Contract Project Manager \u2013 Mechanical Engineering",
            "employer": None,
            "agency": "Matchtech",
            "location_raw": "Manchester, Greater Manchester",
            "posted_at": "2026-06-10T09:00:00+01:00",
            "description_raw": (
                "Leading mechanical contractor seeks an experienced Project Manager "
                "for a 6-month outside-IR35 contract in Manchester. "
                "Managing delivery of mechanical systems (HVAC, pipework, plant) "
                "on a large commercial refurbishment. "
                "Rate: \u00a3580\u2013\u00a3620 per day depending on experience. "
                "Start: 14 July 2026. "
                "Requirements: Prince2/APM qualified; mechanical engineering background; "
                "strong stakeholder management; NEC3/4 contract experience."
            ),
            "contract_type_raw": "Contract",
            "salary_raw": "\u00a3580 - \u00a3620 per day",
        }
        base.update(overrides)
        if _MODELS_AVAILABLE and RawListing is not None:
            try:
                return RawListing(**base)
            except Exception:
                pass
        return base

    return _factory


@pytest.fixture()
def sample_normalized_listing() -> Callable[..., Any]:
    """
    Factory fixture that returns a callable producing NormalizedListing-like dicts
    (or model instances when mechpm.models is available).
    Keyword args override any default field.

    Usage::

        def test_something(sample_normalized_listing):
            listing = sample_normalized_listing(day_rate_min=700.0)
    """

    def _factory(**overrides: Any) -> Any:
        base_id = str(uuid.uuid4())
        base: dict[str, Any] = {
            "listing_id": base_id,
            "source": "reed",
            "source_url": "https://www.reed.co.uk/jobs/contract-project-manager/99000001",
            "source_listing_id": "99000001",
            "title": "Contract Project Manager \u2013 Mechanical Engineering",
            "employer": None,
            "agency": "Matchtech",
            "location": "Manchester, Greater Manchester",
            "location_normalized": "Manchester",
            "country": "GB",
            "posted_at": "2026-06-10T09:00:00+01:00",
            "start_date_raw": "14 July 2026",
            "start_date": "2026-07-14",
            "asap_flag": False,
            "duration_raw": "6 months",
            "duration_weeks": 26,
            "day_rate_min": 580.0,
            "day_rate_max": 620.0,
            "rate_currency": "GBP",
            "rate_period": "day",
            "ir35_status": "outside",
            "contract_type": "contract",
            "remote_policy": "onsite",
            "description_raw": "Mechanical PM contract Manchester.",
            "description_clean": "Experienced Project Manager for 6-month outside-IR35 contract.",
            "sector": "generalist",
            "source_urls": [
                "https://www.reed.co.uk/jobs/contract-project-manager/99000001"
            ],
            "discovered_at": "2026-06-12T17:00:00+01:00",
            "last_seen_at": "2026-06-12T17:00:00+01:00",
        }
        base.update(overrides)
        if _MODELS_AVAILABLE and NormalizedListing is not None:
            try:
                return NormalizedListing(**base)
            except Exception:
                pass
        return base

    return _factory


# ---------------------------------------------------------------------------
# Synthetic Settings fixture — drives adapter smoke tests via _build_adapters()
# ---------------------------------------------------------------------------
try:
    from mechpm.config import Settings, SourceConfig  # type: ignore

    _CONFIG_AVAILABLE = True
except ImportError:
    _CONFIG_AVAILABLE = False
    Settings = None  # type: ignore
    SourceConfig = None  # type: ignore


@pytest.fixture(scope="session")
def synthetic_settings():
    """
    Minimal-valid Settings object for all 7 sources.

    Lets _build_adapters(settings) instantiate every adapter without real
    credentials or a live config.toml.  Reed uses a placeholder api_key;
    StepStone sources carry domain/search_path in SourceConfig extra fields.
    """
    if not _CONFIG_AVAILABLE:
        pytest.skip("mechpm.config not available — skipping adapter fixtures")
    return Settings(
        reed_api_key="test-key-placeholder",
        sources={
            "reed": SourceConfig(
                enabled=True,
                crawl_delay=0,
                keywords="x",
                location="UK",
                results_to_take=1,
                safety_cap=1,
            ),
            "totaljobs": SourceConfig(
                enabled=True,
                crawl_delay=0,
                domain="www.totaljobs.com",
                search_path="/jobs/x",
            ),
            "cwjobs": SourceConfig(
                enabled=True,
                crawl_delay=0,
                domain="www.cwjobs.co.uk",
                search_path="/jobs/x",
            ),
            "railwaypeople": SourceConfig(enabled=True, crawl_delay=0),
            "energy_jobline": SourceConfig(enabled=True, crawl_delay=0),
            "the_engineer": SourceConfig(enabled=True, crawl_delay=0),
            "aviation_job_search": SourceConfig(enabled=True, crawl_delay=0),
            "adzuna": SourceConfig(
                enabled=True,
                crawl_delay=0,
                keywords="project manager mechanical engineering",
                country="gb",
                results_per_page=5,
                safety_cap=5,
            ),
        },
    )


@pytest.fixture(scope="session")
def all_adapters_by_name(synthetic_settings):
    """
    Build all adapters via _build_adapters(synthetic_settings) and return a
    dict keyed by adapter.name.  Session-scoped so construction runs once.
    """
    try:
        from mechpm.cli import _build_adapters  # type: ignore
    except ImportError:
        pytest.skip("mechpm.cli._build_adapters not available — skipping")
    adapters = _build_adapters(synthetic_settings)
    return {a.name: a for a in adapters}


# ---------------------------------------------------------------------------
# Gold-set helpers (usable from any test module)
# ---------------------------------------------------------------------------
GOLD_SET_DIR = Path(__file__).parent / "fixtures" / "gold_set"


def load_gold_fixture(path: Path) -> dict[str, Any]:
    """Load and return a JSON fixture file as a dict."""
    return json.loads(path.read_text(encoding="utf-8"))


def iter_category_pairs(category: str):
    """
    Yield (raw_path, expected_path) tuples for all fixtures in a category
    subdirectory (positive / negative / edge_cases / duplicate_pairs).
    """
    d = GOLD_SET_DIR / category
    if not d.exists():
        return
    for raw_file in sorted(d.glob("*.json")):
        if raw_file.name.endswith(".expected.json") or raw_file.name.endswith(
            ".dedup_expected.json"
        ):
            continue
        expected_file = raw_file.parent / (raw_file.stem + ".expected.json")
        if expected_file.exists():
            yield raw_file, expected_file
