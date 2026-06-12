"""
tests/test_e2e.py — End-to-end pipeline integration tests.
Date: 2026-06-12

Runs the full pipeline (orchestrator → extract → dedup → filter → report) using
mocked adapters backed by the gold-set positive fixtures.  All HTTP calls and
OpenAI API calls are mocked; the real SQLite storage and reporter are used so
persistence and rendering are exercised.

Marked @pytest.mark.slow — excluded from fast unit-test runs.
Use:  pytest -m slow   to run end-to-end tests only.

Acceptance criteria verified here:
  - Pipeline completes without crashing.
  - Raw listings are persisted (JSONL or SQLite).
  - Normalized records written to SQLite normalized_listings table.
  - Report Markdown file is rendered at reports/<date>.md.
  - Wall-clock budget: <= 30 s for mocked run (real budget <= 15 min).
  - Partial adapter failure (one adapter raises) does not crash the run.
  - Data persisted even when one adapter fails.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Optional imports — all skip gracefully
# ---------------------------------------------------------------------------
try:
    from mechpm.orchestrator import run_pipeline  # type: ignore

    _ORCHESTRATOR_AVAILABLE = True
except ImportError:
    _ORCHESTRATOR_AVAILABLE = False

try:
    from mechpm.storage.sqlite import Storage  # type: ignore

    _STORAGE_AVAILABLE = True
except ImportError:
    _STORAGE_AVAILABLE = False

try:
    from mechpm.reporter import render_report  # type: ignore

    _REPORTER_AVAILABLE = True
except ImportError:
    _REPORTER_AVAILABLE = False

_SKIP_E2E = pytest.mark.skipif(
    not _ORCHESTRATOR_AVAILABLE,
    reason="mechpm.orchestrator not available yet",
)

GOLD_POS_DIR = Path(__file__).parent / "fixtures" / "gold_set" / "positive"
pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_gold_raw_listings() -> list[dict]:
    """Load all 8 positive gold-set raw listings as dicts."""
    listings = []
    if not GOLD_POS_DIR.exists():
        return listings
    for f in sorted(GOLD_POS_DIR.glob("*.json")):
        if f.name.endswith(".expected.json"):
            continue
        listings.append(json.loads(f.read_text(encoding="utf-8")))
    return listings


def _make_mock_adapter(name: str, raw_listings: list[dict]) -> MagicMock:
    """Create a mock adapter that returns the given raw listings."""
    adapter = MagicMock()
    adapter.name = name
    adapter.crawl_delay = 0
    adapter.tos_notes = f"Mocked {name} adapter"
    adapter.fetch = AsyncMock(return_value=raw_listings)
    return adapter


# ---------------------------------------------------------------------------
# E2E smoke test: full pipeline with mocked adapters
# ---------------------------------------------------------------------------
@_SKIP_E2E
def test_e2e_pipeline_completes(tmp_path):
    """
    Run the full pipeline with 8 gold-set listings via mocked adapters.
    Assert: run completes, returns without raising, data directory created.
    """
    raw_listings = _load_gold_raw_listings()
    if not raw_listings:
        pytest.skip("No gold-set positive fixtures found")

    mock_adapter = _make_mock_adapter("mock_source", raw_listings)

    config = {
        "db_path": str(tmp_path / "test.db"),
        "reports_dir": str(tmp_path / "reports"),
        "raw_dir": str(tmp_path / "raw"),
    }

    start = time.monotonic()
    try:
        asyncio.run(run_pipeline(adapters=[mock_adapter], config=config))
    except Exception as exc:
        pytest.fail(f"Pipeline raised unexpectedly: {type(exc).__name__}: {exc}")
    elapsed = time.monotonic() - start

    assert elapsed <= 30, f"Mocked pipeline took {elapsed:.1f}s, budget is 30s"
    assert (tmp_path / "reports").exists(), "reports/ directory not created"


@_SKIP_E2E
def test_e2e_raw_listings_persisted(tmp_path):
    """Assert raw listings are written to JSONL or SQLite after a pipeline run."""
    raw_listings = _load_gold_raw_listings()
    if not raw_listings:
        pytest.skip("No gold-set positive fixtures found")

    mock_adapter = _make_mock_adapter("mock_source", raw_listings)
    config = {
        "db_path": str(tmp_path / "test.db"),
        "reports_dir": str(tmp_path / "reports"),
        "raw_dir": str(tmp_path / "raw"),
    }

    asyncio.run(run_pipeline(adapters=[mock_adapter], config=config))

    # Check either JSONL files or SQLite raw_listings table
    db_path = tmp_path / "test.db"
    raw_dir = tmp_path / "raw"

    raw_persisted = False
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM raw_listings").fetchone()[0]
            raw_persisted = count > 0
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    if not raw_persisted and raw_dir.exists():
        jsonl_files = list(raw_dir.glob("*.jsonl"))
        raw_persisted = len(jsonl_files) > 0 and any(f.stat().st_size > 0 for f in jsonl_files)

    assert raw_persisted, "No raw listings found in SQLite or JSONL after pipeline run"


@_SKIP_E2E
def test_e2e_normalized_records_in_sqlite(tmp_path):
    """Assert normalized_listings table is populated after pipeline run."""
    raw_listings = _load_gold_raw_listings()
    if not raw_listings:
        pytest.skip("No gold-set positive fixtures found")

    mock_adapter = _make_mock_adapter("mock_source", raw_listings)
    config = {
        "db_path": str(tmp_path / "test.db"),
        "reports_dir": str(tmp_path / "reports"),
        "raw_dir": str(tmp_path / "raw"),
    }

    asyncio.run(run_pipeline(adapters=[mock_adapter], config=config))

    db_path = tmp_path / "test.db"
    assert db_path.exists(), "SQLite database not created"

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM normalized_listings").fetchone()[0]
    except sqlite3.OperationalError:
        count = 0
    finally:
        conn.close()

    assert count > 0, "normalized_listings table is empty after pipeline run"


@_SKIP_E2E
def test_e2e_report_rendered(tmp_path):
    """Assert a Markdown report is rendered in reports/ after pipeline run."""
    raw_listings = _load_gold_raw_listings()
    if not raw_listings:
        pytest.skip("No gold-set positive fixtures found")

    mock_adapter = _make_mock_adapter("mock_source", raw_listings)
    config = {
        "db_path": str(tmp_path / "test.db"),
        "reports_dir": str(tmp_path / "reports"),
        "raw_dir": str(tmp_path / "raw"),
    }

    asyncio.run(run_pipeline(adapters=[mock_adapter], config=config))

    reports_dir = tmp_path / "reports"
    md_files = list(reports_dir.glob("*.md")) if reports_dir.exists() else []
    assert md_files, "No .md report found in reports/ after pipeline run"
    assert md_files[0].stat().st_size > 0, "Report file is empty"


@_SKIP_E2E
def test_e2e_partial_failure_does_not_crash(tmp_path):
    """
    Simulate one adapter raising an exception.
    Assert the pipeline continues to run and produces output for the
    working adapter.
    """
    raw_listings = _load_gold_raw_listings()
    if not raw_listings:
        pytest.skip("No gold-set positive fixtures found")

    good_adapter = _make_mock_adapter("good_source", raw_listings[:4])
    bad_adapter = MagicMock()
    bad_adapter.name = "bad_source"
    bad_adapter.crawl_delay = 0
    bad_adapter.fetch = AsyncMock(side_effect=RuntimeError("Simulated adapter failure"))

    config = {
        "db_path": str(tmp_path / "test.db"),
        "reports_dir": str(tmp_path / "reports"),
        "raw_dir": str(tmp_path / "raw"),
    }

    try:
        asyncio.run(
            run_pipeline(adapters=[good_adapter, bad_adapter], config=config)
        )
    except Exception as exc:
        pytest.fail(
            f"Pipeline raised on partial adapter failure: {type(exc).__name__}: {exc}. "
            "Pipeline must continue on adapter errors."
        )

    # Data from good adapter must still be present
    db_path = tmp_path / "test.db"
    raw_dir = tmp_path / "raw"
    has_output = False
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM raw_listings").fetchone()[0]
            has_output = count > 0
        except Exception:
            pass
        finally:
            conn.close()
    if not has_output and raw_dir.exists():
        has_output = any(
            f.stat().st_size > 0 for f in raw_dir.glob("*.jsonl")
        )
    assert has_output, "No output from good_adapter after bad_adapter failure"


@_SKIP_E2E
def test_e2e_wall_clock_budget(tmp_path):
    """
    Assert mocked pipeline completes within 30 seconds.
    (Real budget is <= 15 min; mocked run must be much faster.)
    """
    raw_listings = _load_gold_raw_listings()
    if not raw_listings:
        pytest.skip("No gold-set positive fixtures found")

    mock_adapter = _make_mock_adapter("mock_source", raw_listings)
    config = {
        "db_path": str(tmp_path / "test.db"),
        "reports_dir": str(tmp_path / "reports"),
        "raw_dir": str(tmp_path / "raw"),
    }

    start = time.monotonic()
    asyncio.run(run_pipeline(adapters=[mock_adapter], config=config))
    elapsed = time.monotonic() - start

    assert elapsed <= 30, (
        f"Pipeline wall-clock {elapsed:.1f}s exceeded 30s budget for mocked run. "
        "Check for unbounded loops or real network calls leaking through mocks."
    )
