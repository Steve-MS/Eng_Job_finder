"""Tests for mechpm.orchestrator."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mechpm.adapters.base import RawListing
from mechpm.extractor.dedup import DedupResult, dedupe_with_groups
from mechpm.orchestrator import _persist_jsonl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_listings(n: int) -> list[RawListing]:
    return [
        RawListing(
            source="test_source",
            source_listing_id=str(i),
            url=f"https://example.com/job/{i}",
            title=f"Test Job {i}",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Item 4: regression test — _persist_jsonl overwrites on second call
# ---------------------------------------------------------------------------

def test_persist_jsonl_overwrites_on_second_run(tmp_path: Path) -> None:
    """Calling _persist_jsonl twice must produce exactly len(listings) lines, not 2×."""
    listings = _make_listings(3)

    _persist_jsonl(tmp_path, "test_source", listings)
    _persist_jsonl(tmp_path, "test_source", listings)

    lines = (tmp_path / "test_source.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, (
        f"Expected 3 lines after two calls (overwrite mode), got {len(lines)}. "
        "This means the file was opened in append mode — fix _persist_jsonl to use 'w'."
    )


# ---------------------------------------------------------------------------
# Item 5: smoke test — rapidfuzz import + dedup emits no WARNING
# ---------------------------------------------------------------------------

def test_rapidfuzz_importable() -> None:
    """rapidfuzz must be importable and JaroWinkler accessible."""
    from rapidfuzz.distance import JaroWinkler  # noqa: F401  # should not raise


def test_dedup_no_rapidfuzz_warning(caplog: pytest.LogCaptureFixture) -> None:
    """dedupe_with_groups([]) must return an empty DedupResult with no rapidfuzz WARNING."""
    with caplog.at_level(logging.WARNING, logger="mechpm.extractor.dedup"):
        result = dedupe_with_groups([])

    assert isinstance(result, DedupResult)
    assert result.listings == []
    assert result.groups == {}

    rapidfuzz_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "rapidfuzz" in r.message.lower()
    ]
    assert rapidfuzz_warnings == [], (
        f"Unexpected rapidfuzz warning(s): {[r.message for r in rapidfuzz_warnings]}"
    )
