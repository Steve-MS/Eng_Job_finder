"""
tests/test_filters.py — Filter classification accuracy tests against the gold set.
Date: 2026-06-12

Runs each gold-set fixture (positive, negative, edge_case) through the four
mandatory filters and asserts precision/recall against acceptance thresholds.

Thresholds (from Arthur's acceptance criteria):
  - contract_filter: precision >= 0.98, recall >= 0.92
  - uk_filter:       precision >= 0.99, recall >= 0.95
  - pm_filter:       precision >= 0.92, recall >= 0.88
  - mech_filter:     precision >= 0.96, recall >= 0.90

Tests skip cleanly when src/mechpm is not yet available.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Optional imports — skip gracefully when modules absent
# ---------------------------------------------------------------------------
try:
    from mechpm.adapters.base import RawListing  # type: ignore
    from mechpm.extractor import extract  # type: ignore
    from mechpm.extractor.filters import (  # type: ignore
        passes_contract,
        passes_uk,
        passes_pm_role,
        passes_mechanical,
    )

    _PIPELINE_AVAILABLE = True
except ImportError:
    _PIPELINE_AVAILABLE = False

GOLD_SET_DIR = Path(__file__).parent / "fixtures" / "gold_set"

# Map from expected.json filter key → filter function
_FILTER_FN_MAP = {
    "contract_filter": lambda lst: passes_contract(lst) if _PIPELINE_AVAILABLE else False,
    "uk_filter": lambda lst: passes_uk(lst) if _PIPELINE_AVAILABLE else False,
    "pm_filter": lambda lst: passes_pm_role(lst) if _PIPELINE_AVAILABLE else False,
    "mech_filter": lambda lst: passes_mechanical(lst) if _PIPELINE_AVAILABLE else False,
}

# Acceptance thresholds
_THRESHOLDS = {
    "contract_filter": {"min_precision": 0.98, "min_recall": 0.92},
    "uk_filter": {"min_precision": 0.99, "min_recall": 0.95},
    "pm_filter": {"min_precision": 0.92, "min_recall": 0.88},
    "mech_filter": {"min_precision": 0.96, "min_recall": 0.90},
}

_SKIP = pytest.mark.skipif(
    not _PIPELINE_AVAILABLE,
    reason="mechpm extractor/models not available yet",
)


# ---------------------------------------------------------------------------
# Load ALL gold-set fixture pairs (positive + negative + edge_cases)
# ---------------------------------------------------------------------------
def _load_all_pairs():
    pairs: list[tuple[str, Path, Path]] = []
    for subdir in ("positive", "negative", "edge_cases"):
        d = GOLD_SET_DIR / subdir
        if not d.exists():
            continue
        for raw_file in sorted(d.glob("*.json")):
            if raw_file.name.endswith(".expected.json"):
                continue
            exp = raw_file.parent / (raw_file.stem + ".expected.json")
            if exp.exists():
                pairs.append((subdir, raw_file, exp))
    return pairs


_ALL_PAIRS = _load_all_pairs()


# ---------------------------------------------------------------------------
# Parametrized per-fixture filter test
# ---------------------------------------------------------------------------
@_SKIP
@pytest.mark.parametrize(
    "subdir,raw_file,expected_file",
    [pytest.param(s, r, e, id=f"{s}/{r.stem}") for s, r, e in _ALL_PAIRS],
)
def test_filter_outcomes(subdir, raw_file, expected_file, metrics):
    """
    For each gold-set listing extract it then check every filter flag
    matches the expected outcome.  Accumulates TP/FP/TN/FN in session metrics.
    """
    raw_data = json.loads(raw_file.read_text(encoding="utf-8"))
    expected_data = json.loads(expected_file.read_text(encoding="utf-8"))
    expected_filters: dict[str, bool] = expected_data.get("filters", {})
    meta = expected_data.get("meta", {})

    raw = RawListing(**raw_data)
    try:
        listing = extract(raw)
    except Exception as exc:
        pytest.fail(f"Extractor raised for {raw_file.name}: {exc}")

    failures: list[str] = []
    for filter_key in _FILTER_FN_MAP:
        expected_outcome = expected_filters.get(filter_key)
        if expected_outcome is None:
            continue

        predicted = bool(_FILTER_FN_MAP[filter_key](listing))
        actual = bool(expected_outcome)

        metrics.record(filter_key, predicted=predicted, actual=actual)

        if predicted != actual:
            fail_reason = meta.get("fail_reason", "?")
            fail_filter = meta.get("fail_filter", "?")
            failures.append(
                f"  {filter_key}: predicted={predicted}, expected={actual}"
                f" | fail_reason={fail_reason}, designated_fail_filter={fail_filter}"
            )

    if failures:
        pytest.fail(
            f"Filter mismatch for {subdir}/{raw_file.stem}:\n" + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Aggregate precision/recall assertions — run AFTER parametrized tests
# ---------------------------------------------------------------------------
def _check_threshold(metrics, dim_key: str, min_p: float, min_r: float) -> None:
    """Assert precision and recall meet thresholds; skip if no data collected."""
    total = metrics.total(dim_key)
    if total == 0:
        pytest.skip(f"No data for {dim_key} — run test_filter_outcomes first")

    prec = metrics.precision(dim_key)
    rec = metrics.recall(dim_key)
    counts = metrics._counts[dim_key]
    errors: list[str] = []

    if prec is None or prec < min_p:
        errors.append(
            f"precision {prec:.3f} < {min_p} (TP={counts['tp']}, FP={counts['fp']})"
        )
    if rec is None or rec < min_r:
        errors.append(
            f"recall {rec:.3f} < {min_r} (TP={counts['tp']}, FN={counts['fn']})"
        )
    if errors:
        pytest.fail(f"{dim_key} threshold not met:\n" + "\n".join(f"  {e}" for e in errors))


@_SKIP
def test_contract_filter_precision_recall(metrics):
    """contract_filter: precision >= 0.98, recall >= 0.92."""
    _check_threshold(metrics, "contract_filter", 0.98, 0.92)


@_SKIP
def test_uk_filter_precision_recall(metrics):
    """uk_filter: precision >= 0.99, recall >= 0.95."""
    _check_threshold(metrics, "uk_filter", 0.99, 0.95)


@_SKIP
def test_pm_filter_precision_recall(metrics):
    """pm_filter: precision >= 0.92, recall >= 0.88."""
    _check_threshold(metrics, "pm_filter", 0.92, 0.88)


@_SKIP
def test_mech_filter_precision_recall(metrics):
    """mech_filter: precision >= 0.96, recall >= 0.90."""
    _check_threshold(metrics, "mech_filter", 0.96, 0.90)


# ---------------------------------------------------------------------------
# Sanity-check: fixture counts match expected distribution
# ---------------------------------------------------------------------------
def test_gold_set_fixture_counts():
    """Assert gold set has the correct number of fixtures per category."""
    counts = {
        "positive": 0,
        "negative": 0,
        "edge_cases": 0,
        "duplicate_pairs": 0,
    }
    for subdir in counts:
        d = GOLD_SET_DIR / subdir
        if not d.exists():
            continue
        counts[subdir] = sum(
            1
            for f in d.glob("*.json")
            if not f.name.endswith(".expected.json")
            and not f.name.endswith(".dedup_expected.json")
        )

    assert counts["positive"] == 18, f"Expected 18 positives, got {counts['positive']}"
    assert counts["negative"] == 20, f"Expected 20 negatives, got {counts['negative']}"
    assert counts["edge_cases"] == 7, f"Expected 7 edge cases, got {counts['edge_cases']}"
    assert counts["duplicate_pairs"] == 6, (
        f"Expected 6 dup-pair files (3 pairs × 2), got {counts['duplicate_pairs']}"
    )


def test_gold_set_expected_files_present():
    """Assert every raw fixture has a sibling .expected.json (or .dedup_expected.json)."""
    missing: list[str] = []
    for subdir in ("positive", "negative", "edge_cases"):
        d = GOLD_SET_DIR / subdir
        if not d.exists():
            continue
        for raw_file in d.glob("*.json"):
            if raw_file.name.endswith(".expected.json"):
                continue
            exp = raw_file.parent / (raw_file.stem + ".expected.json")
            if not exp.exists():
                missing.append(str(raw_file))
    for raw_file in (GOLD_SET_DIR / "duplicate_pairs").glob("*.json"):
        if raw_file.name.endswith(".dedup_expected.json"):
            continue
        exp = raw_file.parent / (raw_file.stem + ".expected.json")
        dedup_exp = raw_file.parent / (raw_file.stem[:-1] + ".dedup_expected.json")
        if not exp.exists() and not dedup_exp.exists():
            missing.append(str(raw_file))
    assert not missing, "Missing expected.json siblings:\n" + "\n".join(missing)


def test_gold_set_expected_json_valid():
    """Assert every .expected.json file is valid JSON with required keys."""
    invalid: list[str] = []
    for d in ["positive", "negative", "edge_cases"]:
        for f in (GOLD_SET_DIR / d).glob("*.expected.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for required_key in ("extracted", "filters", "meta"):
                    if required_key not in data:
                        invalid.append(f"{f.name}: missing key '{required_key}'")
            except json.JSONDecodeError as exc:
                invalid.append(f"{f.name}: JSON parse error: {exc}")
    assert not invalid, "Invalid expected files:\n" + "\n".join(invalid)


# ---------------------------------------------------------------------------
# Dedicated unit tests for geo-detection changes (2026-06-14)
# ---------------------------------------------------------------------------

@_SKIP
def test_passes_uk_unknown_country_adds_sanity_flag():
    """When location is empty and no geo signal exists, passes_uk rejects and flags."""
    from mechpm.models import NormalizedListing
    from mechpm.extractor.filters import passes_uk

    listing = NormalizedListing(
        source="test",
        title="Project Manager",
        location="",
        country="GB",
        contract_type="contract",
    )
    result = passes_uk(listing)
    assert result is False
    assert "country_unknown_assumed_non_uk" in listing.sanity_flags


@_SKIP
def test_passes_uk_mena_in_title_rejected_without_sanity_flag():
    """MENA signal in title causes rejection; should NOT add the unknown-country flag."""
    from mechpm.models import NormalizedListing
    from mechpm.extractor.filters import passes_uk

    listing = NormalizedListing(
        source="test",
        title="Project Manager (High Speed Rail) - MENA Region",
        location="",
        country="GB",
        contract_type="contract",
    )
    result = passes_uk(listing)
    assert result is False
    assert "country_unknown_assumed_non_uk" not in listing.sanity_flags


@_SKIP
def test_passes_uk_dubai_in_description_rejected():
    """Dubai signal in description causes rejection even with empty location."""
    from mechpm.models import NormalizedListing
    from mechpm.extractor.filters import passes_uk

    listing = NormalizedListing(
        source="test",
        title="Senior Project Manager - Rail",
        location="",
        country="GB",
        contract_type="contract",
        description_raw="The role is based in Dubai supporting a regional rail programme.",
    )
    assert passes_uk(listing) is False


@_SKIP
def test_passes_uk_clear_uk_location_passes():
    """Clear UK location still passes after the default-reject change (regression guard)."""
    from mechpm.models import NormalizedListing
    from mechpm.extractor.filters import passes_uk

    listing = NormalizedListing(
        source="test",
        title="Mechanical Project Manager",
        location="Manchester, UK",
        country="GB",
        contract_type="contract",
    )
    assert passes_uk(listing) is True


@_SKIP
def test_detect_country_scans_title_when_location_absent():
    """detect_country returns non-UK code from title when location_raw is empty."""
    from mechpm.extractor.regex_fields import detect_country

    assert detect_country("", title="PM – MENA Region") == "AE"
    assert detect_country(None, title="Senior PM - Frankfurt office") == "DE"
    assert detect_country("", title="Project Manager", description="based in Dubai") == "AE"


@_SKIP
def test_detect_country_location_takes_priority():
    """Location signal overrides any contrary title/description signal."""
    from mechpm.extractor.regex_fields import detect_country

    # UK location wins even if title mentions a non-UK city
    assert detect_country("London, UK", title="Dubai Office") == "GB"
    # Non-UK location wins
    assert detect_country("Dubai, UAE", title="Manchester office") == "AE"


# ---------------------------------------------------------------------------
# Hard-reject tests for explicitly known non-UK countries (2026-06-15)
# ---------------------------------------------------------------------------

@_SKIP
def test_passes_uk_cairo_egypt_hard_rejected_no_flag():
    """Cairo, Egypt explicit location → hard-reject via _KNOWN_NON_UK_CODES, NO sanity flag."""
    from mechpm.models import NormalizedListing
    from mechpm.extractor.filters import passes_uk

    listing = NormalizedListing(
        source="railwaypeople",
        title="Project Manager (High Speed Rail) - Egypt",
        location="Cairo, Cairo Governorate, Egypt",
        country="EG",
        contract_type="contract",
    )
    result = passes_uk(listing)
    assert result is False
    assert "country_unknown_assumed_non_uk" not in listing.sanity_flags


@_SKIP
def test_passes_uk_new_york_usa_hard_rejected_no_flag():
    """New York, USA explicit location → hard-reject via _KNOWN_NON_UK_CODES, NO sanity flag."""
    from mechpm.models import NormalizedListing
    from mechpm.extractor.filters import passes_uk

    listing = NormalizedListing(
        source="totaljobs",
        title="Project Manager - Mechanical Systems",
        location="New York, NY, USA",
        country="US",
        contract_type="contract",
    )
    result = passes_uk(listing)
    assert result is False
    assert "country_unknown_assumed_non_uk" not in listing.sanity_flags


@_SKIP
def test_passes_uk_dubai_uae_hard_rejected_no_flag():
    """Dubai, UAE explicit location → hard-reject via _KNOWN_NON_UK_CODES, NO sanity flag."""
    from mechpm.models import NormalizedListing
    from mechpm.extractor.filters import passes_uk

    listing = NormalizedListing(
        source="totaljobs",
        title="Project Manager - Mechanical Construction",
        location="Dubai, UAE",
        country="AE",
        contract_type="contract",
    )
    result = passes_uk(listing)
    assert result is False
    assert "country_unknown_assumed_non_uk" not in listing.sanity_flags


@_SKIP
def test_detect_country_egypt_from_location():
    """detect_country returns 'EG' for an explicit Cairo/Egypt location string."""
    from mechpm.extractor.regex_fields import detect_country

    assert detect_country("Cairo, Cairo Governorate, Egypt") == "EG"
    assert detect_country("Alexandria, Egypt") == "EG"
    assert detect_country("Cairo") == "EG"

