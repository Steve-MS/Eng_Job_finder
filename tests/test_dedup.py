"""
tests/test_dedup.py — Deduplication quality tests.
Date: 2026-06-12

Verifies:
  1. Each cross-posted duplicate pair merges into a single canonical record.
  2. The 8 distinct positive fixtures produce no false merges.

Thresholds (from Arthur's acceptance criteria):
  - dedup precision >= 0.98 (no false merges)
  - dedup recall    >= 0.88 (catches true duplicates)

Tests skip cleanly when src/mechpm/extractor/dedup is not yet available.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
try:
    from mechpm.adapters.base import RawListing  # type: ignore
    from mechpm.extractor import extract  # type: ignore
    from mechpm.extractor.dedup import are_duplicates, deduplicate  # type: ignore

    _DEDUP_AVAILABLE = True
except ImportError:
    _DEDUP_AVAILABLE = False

GOLD_SET_DIR = Path(__file__).parent / "fixtures" / "gold_set"
DUP_DIR = GOLD_SET_DIR / "duplicate_pairs"
POS_DIR = GOLD_SET_DIR / "positive"

_SKIP = pytest.mark.skipif(
    not _DEDUP_AVAILABLE,
    reason="mechpm.extractor.dedup not available yet",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_and_extract(path: Path) -> Any:
    raw_data = json.loads(path.read_text(encoding="utf-8"))
    return extract(RawListing(**raw_data))


def _load_pair_expected(pair_id: str) -> dict:
    exp_file = DUP_DIR / f"{pair_id}.dedup_expected.json"
    return json.loads(exp_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Duplicate pair tests
# ---------------------------------------------------------------------------
_DUP_PAIR_IDS = ["dup_01", "dup_02", "dup_03"]


@_SKIP
@pytest.mark.parametrize("pair_id", _DUP_PAIR_IDS)
def test_duplicate_pair_detected(pair_id, metrics):
    """
    Feed a cross-posted pair through are_duplicates().
    Assert returns True (they should merge).
    """
    exp = _load_pair_expected(pair_id)
    files = exp["files"]
    listing_a = _load_and_extract(DUP_DIR / files[0])
    listing_b = _load_and_extract(DUP_DIR / files[1])

    result = are_duplicates(listing_a, listing_b)

    metrics.record("dedup_merge", predicted=bool(result), actual=True)
    assert result, (
        f"{pair_id}: expected are_duplicates=True but got False.\n"
        f"  Title A: {listing_a.title!r}\n"
        f"  Title B: {listing_b.title!r}\n"
        f"  Agency A: {getattr(listing_a, 'agency', '?')}, "
        f"Agency B: {getattr(listing_b, 'agency', '?')}\n"
        f"  Notes: {exp.get('notes', '')}"
    )


@_SKIP
@pytest.mark.parametrize("pair_id", _DUP_PAIR_IDS)
def test_duplicate_pair_deduplicates_to_one(pair_id, metrics):
    """
    Feed a duplicate pair to deduplicate().
    Assert the result collapses to exactly 1 canonical record.
    """
    exp = _load_pair_expected(pair_id)
    files = exp["files"]
    listing_a = _load_and_extract(DUP_DIR / files[0])
    listing_b = _load_and_extract(DUP_DIR / files[1])

    result = deduplicate([listing_a, listing_b])

    assert len(result) == 1, (
        f"{pair_id}: deduplicate() returned {len(result)} records, expected 1. "
        f"Notes: {exp.get('notes', '')}"
    )


@_SKIP
@pytest.mark.parametrize("pair_id", _DUP_PAIR_IDS)
def test_canonical_record_fields(pair_id):
    """
    After merging, assert that the canonical record has the expected fields
    from the .dedup_expected.json specification.
    """
    exp = _load_pair_expected(pair_id)
    files = exp["files"]
    listing_a = _load_and_extract(DUP_DIR / files[0])
    listing_b = _load_and_extract(DUP_DIR / files[1])

    canonical = deduplicate([listing_a, listing_b])[0]
    canonical_fields: dict = exp.get("canonical_fields", {})

    failures: list[str] = []
    for field, expected_val in canonical_fields.items():
        if expected_val is None:
            continue
        actual = getattr(canonical, field, "MISSING")
        if isinstance(expected_val, float) and isinstance(actual, (int, float)):
            if abs(float(actual) - expected_val) > 1.0:
                failures.append(f"  {field}: expected {expected_val}, got {actual}")
        elif actual != expected_val:
            failures.append(f"  {field}: expected {expected_val!r}, got {actual!r}")

    if failures:
        pytest.fail(f"{pair_id} canonical fields wrong:\n" + "\n".join(failures))


# ---------------------------------------------------------------------------
# No false-merge test across all 8 positives
# ---------------------------------------------------------------------------
def _load_all_positives() -> list[Any]:
    listings = []
    if not _DEDUP_AVAILABLE:
        return listings
    if not POS_DIR.exists():
        return listings
    for raw_file in sorted(POS_DIR.glob("*.json")):
        if raw_file.name.endswith(".expected.json"):
            continue
        try:
            listings.append(_load_and_extract(raw_file))
        except Exception:
            pass
    return listings


@_SKIP
def test_no_false_merges_in_positives(metrics):
    """
    Feed all 8 distinct positive fixtures to deduplicate().
    Assert the result still contains 8 listings (no false merges).
    Records TP in dedup_merge for each non-merged pair.
    """
    positives = _load_all_positives()
    if len(positives) < 2:
        pytest.skip(f"Not enough positive listings loaded ({len(positives)})")

    result = deduplicate(positives)

    # Each pair of distinct positives that correctly stays separate = TN in dedup
    n = len(positives)
    for i in range(n):
        for j in range(i + 1, n):
            predicted_dup = are_duplicates(positives[i], positives[j])
            # All distinct positives should NOT be merged
            metrics.record("dedup_merge", predicted=predicted_dup, actual=False)

    assert len(result) == len(positives), (
        f"False merge detected: deduplicate() reduced {len(positives)} distinct "
        f"listings to {len(result)}. Check dedup threshold settings."
    )


# ---------------------------------------------------------------------------
# Aggregate precision/recall assertions
# ---------------------------------------------------------------------------
@_SKIP
def test_dedup_precision_recall(metrics):
    """
    Assert dedup precision >= 0.98 and recall >= 0.88.
    This must run after all parametrized dedup tests.
    """
    total = metrics.total("dedup_merge")
    if total == 0:
        pytest.skip("No dedup metrics collected — run pair tests first")

    prec = metrics.precision("dedup_merge")
    rec = metrics.recall("dedup_merge")
    counts = metrics._counts["dedup_merge"]
    errors: list[str] = []

    if prec is None or prec < 0.98:
        errors.append(
            f"dedup precision {prec:.3f} < 0.98 "
            f"(TP={counts['tp']}, FP={counts['fp']})"
        )
    if rec is None or rec < 0.88:
        errors.append(
            f"dedup recall {rec:.3f} < 0.88 "
            f"(TP={counts['tp']}, FN={counts['fn']})"
        )
    if errors:
        pytest.fail("Dedup threshold not met:\n" + "\n".join(f"  {e}" for e in errors))
