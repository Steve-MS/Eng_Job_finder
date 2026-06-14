"""
tests/test_pipeline_e2e.py — Pipeline integration smoke tests.
Date: 2026-06-14

Tests process_and_report() end-to-end using synthetic fixture JSONL:
  1. Produces SQLite DB and Markdown report from fixture JSONL.
  2. Idempotency: running twice increments times_seen to 2, no duplicate rows.
  3. Quarantine: a malformed JSONL line goes to quarantine; valid listings proceed.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from mechpm.adapters.base import RawListing
from mechpm.pipeline import PipelineResult, process_and_report

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_TODAY = "2026-06-14"
_SINCE = date(2026, 6, 7)

_GOOD_DESCRIPTION = (
    "Senior Project Manager required for a 6-month outside-IR35 contract in Manchester. "
    "Managing delivery of mechanical systems — HVAC, pipework, and plant overhaul. "
    "Candidates must have mechanical engineering background, Prince2/APM, and proven "
    "stakeholder management experience. Rate £550–£650/day. Start ASAP."
)


def _raw_listing(idx: int, source: str = "test_source") -> RawListing:
    """Build a RawListing that should pass all four pipeline filters."""
    return RawListing(
        source=source,
        source_listing_id=f"PIPE-{idx:03d}",
        url=f"https://test.example.com/jobs/{idx}",
        title="Senior Project Manager – Mechanical Engineering",
        employer="Siemens Energy",
        location_raw="Manchester",
        posted_at=datetime.now(timezone.utc),
        contract_type_raw="contract",
        salary_raw="£550 - £650 per day",
        description_raw=_GOOD_DESCRIPTION,
    )


def _write_jsonl(path: Path, listings: list[RawListing]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for listing in listings:
            fh.write(listing.model_dump_json() + "\n")


def _fixture_raw_dir(tmp_path: Path, date_str: str = _TODAY) -> Path:
    """Return a raw_dir Path and populate it with 3 good fixture listings."""
    raw_dir = tmp_path / "raw"
    jsonl_path = raw_dir / date_str / "test_source.jsonl"
    listings = [_raw_listing(i) for i in range(1, 4)]
    _write_jsonl(jsonl_path, listings)
    return raw_dir


# ---------------------------------------------------------------------------
# Test 1: Basic run — produces SQLite DB and Markdown report
# ---------------------------------------------------------------------------

class TestBasicRun:
    def test_produces_sqlite_db(self, tmp_path: Path) -> None:
        raw_dir = _fixture_raw_dir(tmp_path)
        db_path = tmp_path / "mechpm.sqlite"
        reports_dir = tmp_path / "reports"

        result = process_and_report(
            date_str=_TODAY,
            since_date=_SINCE,
            raw_dir=raw_dir,
            db_path=db_path,
            reports_dir=reports_dir,
        )

        assert isinstance(result, PipelineResult)
        assert result.fetched == 3
        assert result.quarantined == 0
        assert db_path.exists(), "SQLite database not created"

    def test_produces_markdown_report(self, tmp_path: Path) -> None:
        raw_dir = _fixture_raw_dir(tmp_path)
        db_path = tmp_path / "mechpm.sqlite"
        reports_dir = tmp_path / "reports"

        result = process_and_report(
            date_str=_TODAY,
            since_date=_SINCE,
            raw_dir=raw_dir,
            db_path=db_path,
            reports_dir=reports_dir,
        )

        assert result.reported is True
        report_path = reports_dir / f"{_TODAY}.md"
        assert report_path.exists(), "Markdown report not created"
        content = report_path.read_text(encoding="utf-8")
        assert len(content) > 100, "Report file is suspiciously small"

    def test_pipeline_counts_sane(self, tmp_path: Path) -> None:
        """extracted + quarantined must equal fetched; stored <= extracted."""
        raw_dir = _fixture_raw_dir(tmp_path)
        db_path = tmp_path / "mechpm.sqlite"
        reports_dir = tmp_path / "reports"

        result = process_and_report(
            date_str=_TODAY,
            since_date=_SINCE,
            raw_dir=raw_dir,
            db_path=db_path,
            reports_dir=reports_dir,
        )

        assert result.extracted + result.quarantined == result.fetched
        assert result.stored <= result.extracted
        assert result.filtered_out >= 0
        assert result.deduped >= 0


# ---------------------------------------------------------------------------
# Test 2: Idempotency — running twice increments times_seen, no duplicate rows
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_times_seen_increments_on_second_run(self, tmp_path: Path) -> None:
        raw_dir = _fixture_raw_dir(tmp_path)
        db_path = tmp_path / "mechpm.sqlite"
        reports_dir = tmp_path / "reports"

        kwargs = dict(
            date_str=_TODAY,
            since_date=_SINCE,
            raw_dir=raw_dir,
            db_path=db_path,
            reports_dir=reports_dir,
        )

        r1 = process_and_report(**kwargs)
        r2 = process_and_report(**kwargs)

        # Both runs must complete without error.
        assert r1.fetched == 3
        assert r2.fetched == 3

        # Row count must not increase on second run.
        conn = sqlite3.connect(str(db_path))
        try:
            total = conn.execute("SELECT COUNT(*) FROM normalized_listings").fetchone()[0]
            times_seen_vals = conn.execute(
                "SELECT times_seen FROM normalized_listings"
            ).fetchall()
        finally:
            conn.close()

        # After two runs there should be no duplicates.
        assert total == r1.stored, (
            f"Row count after 2 runs ({total}) != row count after 1 run ({r1.stored})"
        )
        # All stored rows should have times_seen == 2.
        if times_seen_vals:
            for (ts,) in times_seen_vals:
                assert ts == 2, f"Expected times_seen=2 after 2 runs, got {ts}"

    def test_no_duplicate_rows_after_two_runs(self, tmp_path: Path) -> None:
        raw_dir = _fixture_raw_dir(tmp_path)
        db_path = tmp_path / "mechpm.sqlite"
        reports_dir = tmp_path / "reports"

        kwargs = dict(
            date_str=_TODAY,
            since_date=_SINCE,
            raw_dir=raw_dir,
            db_path=db_path,
            reports_dir=reports_dir,
        )
        process_and_report(**kwargs)
        process_and_report(**kwargs)

        conn = sqlite3.connect(str(db_path))
        try:
            total = conn.execute("SELECT COUNT(*) FROM normalized_listings").fetchone()[0]
            distinct = conn.execute(
                "SELECT COUNT(DISTINCT listing_id) FROM normalized_listings"
            ).fetchone()[0]
        finally:
            conn.close()

        assert total == distinct, (
            f"Duplicate rows detected: total={total}, distinct={distinct}"
        )


# ---------------------------------------------------------------------------
# Test 3: Quarantine — malformed listing goes to quarantine, others proceed
# ---------------------------------------------------------------------------

class TestQuarantine:
    def test_malformed_line_goes_to_quarantine(self, tmp_path: Path) -> None:
        raw_dir = tmp_path / "raw"
        date_dir = raw_dir / _TODAY
        date_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = date_dir / "test_source.jsonl"

        # Two good listings + one line missing the required 'title' field.
        good1 = _raw_listing(1)
        good2 = _raw_listing(2)
        bad_line = json.dumps(
            {"source": "test_source", "source_listing_id": "BAD-001",
             "url": "https://test.com/bad", "title": None}  # null title → ValidationError
        )

        with open(jsonl_path, "w", encoding="utf-8") as fh:
            fh.write(good1.model_dump_json() + "\n")
            fh.write(bad_line + "\n")
            fh.write(good2.model_dump_json() + "\n")

        db_path = tmp_path / "mechpm.sqlite"
        reports_dir = tmp_path / "reports"
        quarantine_dir = tmp_path / "quarantine"

        result = process_and_report(
            date_str=_TODAY,
            since_date=_SINCE,
            raw_dir=raw_dir,
            db_path=db_path,
            reports_dir=reports_dir,
            quarantine_dir=quarantine_dir,
        )

        assert result.fetched == 3
        assert result.quarantined == 1

        q_file = quarantine_dir / _TODAY / "test_source.jsonl"
        assert q_file.exists(), "Quarantine file not created"
        q_entries = [
            json.loads(line) for line in q_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(q_entries) == 1, f"Expected 1 quarantine entry, got {len(q_entries)}"
        assert "error" in q_entries[0]

    def test_valid_listings_proceed_despite_quarantine(self, tmp_path: Path) -> None:
        """The pipeline must not abort when one listing is quarantined."""
        raw_dir = tmp_path / "raw"
        date_dir = raw_dir / _TODAY
        date_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = date_dir / "test_source.jsonl"

        good = _raw_listing(10)
        bad_line = '{"not_a_raw_listing": true}'  # completely wrong shape

        with open(jsonl_path, "w", encoding="utf-8") as fh:
            fh.write(good.model_dump_json() + "\n")
            fh.write(bad_line + "\n")

        result = process_and_report(
            date_str=_TODAY,
            since_date=_SINCE,
            raw_dir=raw_dir,
            db_path=tmp_path / "mechpm.sqlite",
            reports_dir=tmp_path / "reports",
            quarantine_dir=tmp_path / "quarantine",
        )

        # Pipeline must complete.
        assert result.fetched == 2
        assert result.quarantined == 1
        # The one good listing must have been extracted (stored depends on filters).
        assert result.extracted == 1
