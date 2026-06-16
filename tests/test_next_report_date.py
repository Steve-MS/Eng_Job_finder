"""tests/test_next_report_date.py — Tests for _next_friday_from().

Verifies that the helper always returns the correct coming Friday
regardless of what day of the week *today* is.

Added 2026-06-16 — bug fix: footer was blindly adding 7 days to
date_range_end instead of finding the next Friday from date.today().
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from mechpm.reporter.render import _next_friday_from


# ---------------------------------------------------------------------------
# Parametrised correctness tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("today,expected_friday", [
    # Tuesday 16 Jun 2026 → Friday 19 Jun 2026 (3 days away)
    (date(2026, 6, 16), date(2026, 6, 19)),
    # Wednesday 17 Jun 2026 → Friday 19 Jun 2026 (2 days away)
    (date(2026, 6, 17), date(2026, 6, 19)),
    # Thursday 18 Jun 2026 → Friday 19 Jun 2026 (1 day away)
    (date(2026, 6, 18), date(2026, 6, 19)),
    # Friday 19 Jun 2026 → *next* Friday 26 Jun 2026 (7 days, not same day)
    (date(2026, 6, 19), date(2026, 6, 26)),
    # Saturday 20 Jun 2026 → Friday 26 Jun 2026 (6 days away)
    (date(2026, 6, 20), date(2026, 6, 26)),
    # Sunday 21 Jun 2026 → Friday 26 Jun 2026 (5 days away)
    (date(2026, 6, 21), date(2026, 6, 26)),
    # Monday 22 Jun 2026 → Friday 26 Jun 2026 (4 days away)
    (date(2026, 6, 22), date(2026, 6, 26)),
])
def test_next_friday_from_parametrised(today: date, expected_friday: date) -> None:
    result = _next_friday_from(today)
    assert result == expected_friday, (
        f"_next_friday_from({today}) → {result}, expected {expected_friday}"
    )
    assert result.weekday() == 4, f"Result {result} is not a Friday (weekday={result.weekday()})"


def test_next_friday_is_always_in_the_future() -> None:
    """The returned date must always be strictly after the input date."""
    for offset in range(7):
        anchor = date(2026, 6, 16) + timedelta(days=offset)
        result = _next_friday_from(anchor)
        assert result > anchor, f"_next_friday_from({anchor}) → {result} is not in the future"


def test_next_friday_from_friday_skips_one_week() -> None:
    """When today IS a Friday the function must skip to the following Friday."""
    friday = date(2026, 6, 19)
    assert friday.weekday() == 4, "Fixture date is not a Friday — fix the test"
    result = _next_friday_from(friday)
    assert result == friday + timedelta(days=7), (
        "When today is Friday, next_friday_from must return 7 days hence"
    )
