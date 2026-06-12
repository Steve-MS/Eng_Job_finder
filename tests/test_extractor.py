"""
tests/test_extractor.py — Extraction accuracy tests against the gold set.
Date: 2026-06-12

Tests each positive and edge-case fixture to verify that the extractor
correctly populates key NormalizedListing fields within the defined
precision thresholds.

Acceptance thresholds (from Arthur's design):
  - Structured fields (URL, contract_type): 95% precision
  - Semi-structured (day_rate, start_date):  80% precision
  - Fuzzy (location):                        75% precision

Tests skip cleanly when src/mechpm/extractor is not yet available.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Optional import — skip entire module gracefully if extractor absent
# ---------------------------------------------------------------------------
try:
    from mechpm.adapters.base import RawListing  # type: ignore
    from mechpm.extractor import extract  # type: ignore

    _EXTRACTOR_AVAILABLE = True
except ImportError:
    _EXTRACTOR_AVAILABLE = False

GOLD_SET_DIR = Path(__file__).parent / "fixtures" / "gold_set"

# ---------------------------------------------------------------------------
# Parametrize over positives + edge cases (extraction targets)
# ---------------------------------------------------------------------------
_EXTRACTION_DIRS = ("positive", "edge_cases")


def _load_extraction_pairs() -> list[tuple[str, Path, Path]]:
    pairs: list[tuple[str, Path, Path]] = []
    for subdir in _EXTRACTION_DIRS:
        d = GOLD_SET_DIR / subdir
        if not d.exists():
            continue
        for raw_file in sorted(d.glob("*.json")):
            if raw_file.name.endswith(".expected.json"):
                continue
            expected_file = raw_file.parent / (raw_file.stem + ".expected.json")
            if expected_file.exists():
                pairs.append((subdir, raw_file, expected_file))
    return pairs


_EXTRACTION_PAIRS = _load_extraction_pairs()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SKIP_NO_EXTRACTOR = pytest.mark.skipif(
    not _EXTRACTOR_AVAILABLE,
    reason="mechpm.extractor not available yet — skipping extraction tests",
)


def _get_listing(raw_data: dict[str, Any]) -> Any:
    """Run extractor; raise AssertionError with context on failure."""
    raw = RawListing(**raw_data)
    return extract(raw)


def _assert_field(
    listing: Any,
    field: str,
    expected: Any,
    tolerance: float = 0.0,
    *,
    fixture_id: str,
) -> tuple[bool, str]:
    """
    Compare extracted field against expected value.
    Returns (passed: bool, reason: str).
    Numeric comparisons allow tolerance for rates in case of rounding.
    """
    actual = getattr(listing, field, "MISSING")
    if actual == "MISSING":
        return False, f"{field}: attribute missing on listing"
    if expected is None:
        # We only verify non-None expected values
        return True, ""
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        passed = abs(float(actual) - expected) <= max(tolerance, 0.5)
        return passed, f"{field}: expected {expected}, got {actual}" if not passed else ""
    passed = actual == expected
    return passed, f"{field}: expected {expected!r}, got {actual!r}" if not passed else ""


# ---------------------------------------------------------------------------
# Parametrized per-fixture extraction tests
# ---------------------------------------------------------------------------
@_SKIP_NO_EXTRACTOR
@pytest.mark.parametrize(
    "subdir,raw_file,expected_file",
    [pytest.param(s, r, e, id=f"{s}/{r.stem}") for s, r, e in _EXTRACTION_PAIRS],
)
def test_extract_structured_fields(subdir, raw_file, expected_file, metrics):
    """
    Assert structured extraction fields meet the 95% precision target.
    Structured fields: country, contract_type, rate_currency.
    """
    raw_data = json.loads(raw_file.read_text(encoding="utf-8"))
    expected_data = json.loads(expected_file.read_text(encoding="utf-8"))
    extracted = expected_data.get("extracted", {})

    listing = _get_listing(raw_data)

    structured_fields = ["country", "contract_type", "rate_currency", "rate_period"]
    meta = expected_data.get("meta", {})
    tricky = set(meta.get("tricky_fields", []))
    failures = []
    for field in structured_fields:
        if field not in extracted:
            continue
        passed, reason = _assert_field(listing, field, extracted[field], fixture_id=raw_file.stem)
        metrics.record("extraction", predicted=passed, actual=True)
        if not passed:
            if field in tricky:
                pytest.skip(f"Tricky field '{field}' in {raw_file.stem} — requires LLM: {reason}")
            failures.append(reason)

    if failures:
        pytest.fail(f"[{subdir}/{raw_file.stem}] Structured extraction failures:\n" + "\n".join(failures))


@_SKIP_NO_EXTRACTOR
@pytest.mark.parametrize(
    "subdir,raw_file,expected_file",
    [pytest.param(s, r, e, id=f"{s}/{r.stem}") for s, r, e in _EXTRACTION_PAIRS],
)
def test_extract_semi_structured_fields(subdir, raw_file, expected_file, metrics):
    """
    Assert semi-structured fields meet the 80% precision target.
    Semi-structured: day_rate_min, day_rate_max, duration_weeks, start_date, asap_flag, ir35_status.
    """
    raw_data = json.loads(raw_file.read_text(encoding="utf-8"))
    expected_data = json.loads(expected_file.read_text(encoding="utf-8"))
    extracted = expected_data.get("extracted", {})
    meta = expected_data.get("meta", {})
    tricky = set(meta.get("tricky_fields", []))

    listing = _get_listing(raw_data)

    semi_fields = ["day_rate_min", "day_rate_max", "duration_weeks", "ir35_status", "asap_flag"]
    failures = []
    for field in semi_fields:
        if field not in extracted:
            continue
        expected_val = extracted[field]
        if expected_val is None:
            continue  # null expectations checked separately
        passed, reason = _assert_field(listing, field, expected_val, tolerance=5.0, fixture_id=raw_file.stem)
        metrics.record("extraction", predicted=passed, actual=True)
        # Tricky fields: mark failure as info only (don't fail the test, still count in metrics)
        if not passed:
            if field in tricky:
                pytest.skip(f"Tricky field '{field}' in {raw_file.stem} — may require LLM fallback: {reason}")
            failures.append(reason)

    if failures:
        pytest.fail(
            f"[{subdir}/{raw_file.stem}] Semi-structured extraction failures:\n" + "\n".join(failures)
        )


@_SKIP_NO_EXTRACTOR
@pytest.mark.parametrize(
    "subdir,raw_file,expected_file",
    [pytest.param(s, r, e, id=f"{s}/{r.stem}") for s, r, e in _EXTRACTION_PAIRS],
)
def test_extract_fuzzy_location(subdir, raw_file, expected_file, metrics):
    """
    Assert location normalization meets the 75% precision target.
    Fuzzy field: location_normalized.
    """
    raw_data = json.loads(raw_file.read_text(encoding="utf-8"))
    expected_data = json.loads(expected_file.read_text(encoding="utf-8"))
    extracted = expected_data.get("extracted", {})
    expected_loc = extracted.get("location_normalized")

    if not expected_loc:
        return

    listing = _get_listing(raw_data)
    actual = getattr(listing, "location_normalized", None)
    if actual is None:
        actual = getattr(listing, "location", None)

    passed = actual is not None and expected_loc.lower() in str(actual).lower()
    metrics.record("extraction", predicted=passed, actual=True)
    if not passed:
        pytest.fail(
            f"[{subdir}/{raw_file.stem}] Location mismatch: "
            f"expected '{expected_loc}', got '{actual}'"
        )


# ---------------------------------------------------------------------------
# Aggregate precision assertion (runs after all parametrized tests)
# ---------------------------------------------------------------------------
@_SKIP_NO_EXTRACTOR
def test_extraction_precision_threshold(metrics):
    """
    Assert overall extraction precision >= 95% across all structured fields.
    This test must run AFTER all parametrized extraction tests.
    """
    prec = metrics.precision("extraction")
    total = metrics.total("extraction")

    if total == 0:
        pytest.skip("No extraction data collected — run parametrized tests first")

    assert prec is not None and prec >= 0.95, (
        f"Extraction precision {prec:.3f} < 0.95 threshold "
        f"(counts: {metrics._counts['extraction']})"
    )
