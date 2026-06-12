#!/usr/bin/env python3
"""
tests/acceptance_gate.py — Mech-PM-Finder acceptance gate runner.
Date: 2026-06-12

Usage:
    python tests/acceptance_gate.py
    python tests/acceptance_gate.py --skip-tests        # read existing metrics only
    python tests/acceptance_gate.py --json-output PATH  # custom JSON report path
    python -m tests.acceptance_gate

Runs the full pytest suite (excluding @pytest.mark.slow by default), reads the
aggregated metrics written by conftest.pytest_sessionfinish, computes the
7-dimension scorecard, prints a pretty console table, writes machine-readable
JSON, and exits non-zero if any dimension fails its threshold.

The 7 acceptance dimensions:
  1. ADAPTER       — all adapter interface + error-handling tests pass
  2. EXTRACTION    — field precision >= 0.95 across structured fields
  3. CONTRACT_FILTER — precision >= 0.98, recall >= 0.92
  4. UK_FILTER     — precision >= 0.99, recall >= 0.95
  5. PM_FILTER     — precision >= 0.92, recall >= 0.88
  6. MECH_FILTER   — precision >= 0.96, recall >= 0.90
  7. DEDUP         — precision >= 0.98, recall >= 0.88
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

TESTS_DIR = Path(__file__).parent
METRICS_FILE = TESTS_DIR / ".test_metrics.json"
DEFAULT_JSON_OUTPUT = TESTS_DIR / "acceptance_report.json"

# ---------------------------------------------------------------------------
# Dimension definitions
# ---------------------------------------------------------------------------
@dataclass
class Dimension:
    name: str
    metrics_key: str
    min_precision: float
    min_recall: float | None = None
    label: str = ""

    def __post_init__(self):
        if not self.label:
            self.label = self.name.replace("_", " ").title()


DIMENSIONS = [
    Dimension("ADAPTER",          "adapter",          min_precision=1.00,  min_recall=None,  label="Adapter interface"),
    Dimension("EXTRACTION",       "extraction",        min_precision=0.95,  min_recall=None,  label="Extraction accuracy"),
    Dimension("CONTRACT_FILTER",  "contract_filter",   min_precision=0.98,  min_recall=0.92,  label="Contract filter"),
    Dimension("UK_FILTER",        "uk_filter",         min_precision=0.99,  min_recall=0.95,  label="UK geo filter"),
    Dimension("PM_FILTER",        "pm_filter",         min_precision=0.92,  min_recall=0.88,  label="PM role filter"),
    Dimension("MECH_FILTER",      "mech_filter",       min_precision=0.96,  min_recall=0.90,  label="Mech domain filter"),
    Dimension("DEDUP",            "dedup_merge",       min_precision=0.98,  min_recall=0.88,  label="Dedup quality"),
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class DimensionResult:
    name: str
    label: str
    passed: bool
    precision: float | None
    recall: float | None
    min_precision: float
    min_recall: float | None
    total_samples: int
    counts: dict = field(default_factory=dict)
    failure_reason: str = ""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
def run_pytest(extra_args: list[str] | None = None) -> int:
    """Run pytest and return exit code."""
    cmd = [
        sys.executable, "-m", "pytest", str(TESTS_DIR),
        "-m", "not slow",          # skip E2E by default
        "--tb=short",
        "-q",
        "--no-header",
    ]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd)
    return result.returncode


def load_metrics() -> dict:
    """Load the .test_metrics.json file written by conftest.pytest_sessionfinish."""
    if not METRICS_FILE.exists():
        return {}
    try:
        return json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def compute_scorecard(metrics: dict) -> list[DimensionResult]:
    results: list[DimensionResult] = []

    for dim in DIMENSIONS:
        dim_data = metrics.get(dim.metrics_key, {})
        prec = dim_data.get("precision")
        rec = dim_data.get("recall")
        total = dim_data.get("total", 0)
        counts = dim_data.get("counts", {})

        failures: list[str] = []
        if total == 0 or prec is None:
            passed = False
            failures.append(f"no data collected (0 samples in '{dim.metrics_key}')")
        else:
            if prec < dim.min_precision:
                failures.append(
                    f"precision {prec:.3f} < {dim.min_precision:.3f} "
                    f"(TP={counts.get('tp',0)}, FP={counts.get('fp',0)})"
                )
            if dim.min_recall is not None and (rec is None or rec < dim.min_recall):
                rec_str = f"{rec:.3f}" if rec is not None else "n/a"
                failures.append(
                    f"recall {rec_str} < {dim.min_recall:.3f} "
                    f"(TP={counts.get('tp',0)}, FN={counts.get('fn',0)})"
                )
            passed = len(failures) == 0

        results.append(DimensionResult(
            name=dim.name,
            label=dim.label,
            passed=passed,
            precision=prec,
            recall=rec,
            min_precision=dim.min_precision,
            min_recall=dim.min_recall,
            total_samples=total,
            counts=counts,
            failure_reason="; ".join(failures) if failures else "PASS",
        ))

    return results


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------
def print_scorecard(results: list[DimensionResult], pytest_exit: int) -> None:
    green = "\033[32m"
    red = "\033[31m"
    yellow = "\033[33m"
    reset = "\033[0m"

    # Detect Windows cmd (no ANSI) vs terminal with color support
    use_color = sys.stdout.isatty()

    def _col(text: str, color: str) -> str:
        return f"{color}{text}{reset}" if use_color else text

    width = 72
    print()
    print("=" * width)
    print("  MECH-PM-FINDER — ACCEPTANCE GATE SCORECARD  (2026-06-12)")
    print("=" * width)
    print(f"  {'Dimension':<22}  {'Status':<8}  {'Precision':>10}  {'Recall':>8}  {'n':>5}")
    print("-" * width)

    for r in results:
        status_str = "✅ PASS" if r.passed else "❌ FAIL"
        status_col = _col(status_str, green if r.passed else red)
        prec_str = f"{r.precision:.3f}" if r.precision is not None else "  n/a"
        rec_str = f"{r.recall:.3f}" if r.recall is not None else "   n/a"
        threshold_str = f"≥{r.min_precision:.2f}"
        rec_threshold_str = f"≥{r.min_recall:.2f}" if r.min_recall else "  n/a"
        print(
            f"  {r.label:<22}  {status_str:<8}  "
            f"{prec_str:>6} ({threshold_str})  "
            f"{rec_str:>5} ({rec_threshold_str})  "
            f"{r.total_samples:>4}"
        )
        if not r.passed:
            print(f"    {_col('→ ' + r.failure_reason, yellow)}")

    print("-" * width)
    all_pass = all(r.passed for r in results)
    if all_pass:
        overall = _col("✅  ALL DIMENSIONS PASS — READY FOR RELEASE", green)
    else:
        fail_count = sum(1 for r in results if not r.passed)
        overall = _col(f"❌  {fail_count} DIMENSION(S) FAIL — DO NOT RELEASE", red)
    print(f"  {overall}")
    print(f"  pytest exit code: {pytest_exit}")
    print("=" * width)
    print()


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------
def write_json_report(
    results: list[DimensionResult],
    pytest_exit: int,
    output_path: Path,
) -> None:
    report = {
        "schema_version": "1.0",
        "date": "2026-06-12",
        "pytest_exit_code": pytest_exit,
        "overall_pass": all(r.passed for r in results),
        "dimensions": [asdict(r) for r in results],
        "thresholds": {
            d.name: {
                "min_precision": d.min_precision,
                "min_recall": d.min_recall,
            }
            for d in DIMENSIONS
        },
    }
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"JSON scorecard written → {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mech-PM-Finder acceptance gate — runs tests and checks thresholds."
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Do not run pytest; read existing .test_metrics.json instead.",
    )
    parser.add_argument(
        "--include-slow",
        action="store_true",
        help="Include @pytest.mark.slow (E2E) tests in the run.",
    )
    parser.add_argument(
        "--json-output",
        default=str(DEFAULT_JSON_OUTPUT),
        help=f"Path for machine-readable JSON report (default: {DEFAULT_JSON_OUTPUT})",
    )
    parser.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        help="Extra arguments forwarded verbatim to pytest.",
    )
    args = parser.parse_args(argv)

    pytest_exit = 0

    if not args.skip_tests:
        print(f"Running test suite (skip_slow={not args.include_slow}) …")
        t0 = time.monotonic()
        extra: list[str] = []
        if args.include_slow:
            extra = ["-m", ""]  # remove the 'not slow' filter
        if args.pytest_args:
            extra.extend(args.pytest_args)
        pytest_exit = run_pytest(extra_args=extra or None)
        elapsed = time.monotonic() - t0
        print(f"pytest finished in {elapsed:.1f}s (exit={pytest_exit})\n")
    else:
        print("--skip-tests: reading existing metrics file …")

    metrics = load_metrics()
    if not metrics:
        print(
            f"WARNING: No metrics found at {METRICS_FILE}. "
            "Run without --skip-tests first, or check conftest.py."
        )

    results = compute_scorecard(metrics)
    print_scorecard(results, pytest_exit)

    output_path = Path(args.json_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_report(results, pytest_exit, output_path)

    overall_pass = all(r.passed for r in results)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
