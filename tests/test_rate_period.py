"""Regression tests for rate_period-aware display, banding, sorting, and flags.

Covers the bug where Ada's rate_parser stores hourly rates in day_rate_min with
rate_period='hour', but the reporter treated all values as £/day.

Added 2026-06-15 — see .squad/decisions/inbox/polly-rate-period-rendering.md.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mechpm.models import NormalizedListing
from mechpm.reporter.domain import effective_day_rate, rate_context
from mechpm.reporter.grouping import get_sanity_reasons, is_premium
from mechpm.reporter.render import _classify_seniority, _rate_str  # noqa: PLC2701


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_listing(**kwargs) -> NormalizedListing:
    defaults: dict = {
        "source": "test",
        "title": "Test PM",
        "location": "Midlands",
        "location_normalized": "midlands",
        "ir35_status": "inside",
    }
    defaults.update(kwargs)
    return NormalizedListing(**defaults)


# ---------------------------------------------------------------------------
# Display: _rate_str unit suffix
# ---------------------------------------------------------------------------

class TestRateStr:
    def test_hourly_single_renders_per_hr(self) -> None:
        listing = _make_listing(day_rate_min=46, rate_period="hour")
        assert _rate_str(listing) == "£46/hr"

    def test_daily_single_renders_per_day(self) -> None:
        listing = _make_listing(day_rate_min=600, rate_period="day")
        assert _rate_str(listing) == "£600/day"

    def test_none_period_defaults_to_per_day(self) -> None:
        listing = _make_listing(day_rate_min=500, rate_period=None)
        assert _rate_str(listing) == "£500/day"

    def test_hourly_range_renders_per_hr(self) -> None:
        listing = _make_listing(day_rate_min=38, day_rate_max=48, rate_period="hour")
        assert _rate_str(listing) == "£38–£48/hr"

    def test_daily_range_renders_per_day(self) -> None:
        listing = _make_listing(day_rate_min=400, day_rate_max=500, rate_period="day")
        assert _rate_str(listing) == "£400–£500/day"

    def test_no_rate_returns_tbc(self) -> None:
        listing = _make_listing()
        assert _rate_str(listing) == "Rate TBC"


# ---------------------------------------------------------------------------
# Normalisation: effective_day_rate()
# ---------------------------------------------------------------------------

class TestEffectiveDayRate:
    def test_hourly_multiplied_by_8(self) -> None:
        listing = _make_listing(day_rate_min=46, rate_period="hour")
        assert effective_day_rate(listing) == pytest.approx(368.0)

    def test_daily_unchanged(self) -> None:
        listing = _make_listing(day_rate_min=600, rate_period="day")
        assert effective_day_rate(listing) == pytest.approx(600.0)

    def test_none_period_treated_as_day(self) -> None:
        listing = _make_listing(day_rate_min=500, rate_period=None)
        assert effective_day_rate(listing) == pytest.approx(500.0)

    def test_prefers_max_over_min(self) -> None:
        listing = _make_listing(day_rate_min=400, day_rate_max=500, rate_period="day")
        assert effective_day_rate(listing) == pytest.approx(500.0)

    def test_hourly_range_uses_max(self) -> None:
        listing = _make_listing(day_rate_min=38, day_rate_max=48, rate_period="hour")
        assert effective_day_rate(listing) == pytest.approx(384.0)  # 48 * 8

    def test_returns_none_when_no_rate(self) -> None:
        listing = _make_listing()
        assert effective_day_rate(listing) is None


# ---------------------------------------------------------------------------
# Band classification: rate_context()
# ---------------------------------------------------------------------------

class TestBanding:
    def test_hourly_46_is_not_below_typical(self) -> None:
        """£46/hr = £368/day equiv — must NOT be labelled 'below typical'."""
        listing = _make_listing(day_rate_min=46, rate_period="hour")
        ctx = rate_context(listing)
        assert "below typical" not in ctx, f"Still 'below typical' after fix: {ctx}"

    def test_hourly_46_is_junior_band(self) -> None:
        """£46/hr × 8 = £368/day — falls in junior band (350–600)."""
        listing = _make_listing(day_rate_min=46, rate_period="hour")
        ctx = rate_context(listing)
        assert "junior-band" in ctx, f"Expected 'junior-band', got: {ctx}"

    def test_daily_46_is_below_typical(self) -> None:
        """£46/day (no rate_period) should still be 'below typical' — regression guard."""
        listing = _make_listing(day_rate_min=46, rate_period=None)
        ctx = rate_context(listing)
        assert "below typical" in ctx, f"Expected 'below typical', got: {ctx}"

    def test_hourly_range_38_48_is_not_below_typical(self) -> None:
        """£38–£48/hr — max=48 × 8 = £384/day → junior-band, NOT below typical."""
        listing = _make_listing(day_rate_min=38, day_rate_max=48, rate_period="hour")
        ctx = rate_context(listing)
        assert "below typical" not in ctx, f"Still 'below typical' after fix: {ctx}"

    def test_hourly_75_is_mid_band(self) -> None:
        """£75/hr × 8 = £600/day — on the boundary of mid-band (480–750)."""
        listing = _make_listing(day_rate_min=75, rate_period="hour")
        ctx = rate_context(listing)
        # £600 sits within mid-band (480–750): 600 < 750*1.10=825 and > 480*0.85=408
        assert "mid-band" in ctx, f"Expected 'mid-band', got: {ctx}"


# ---------------------------------------------------------------------------
# Premium-rate flag: is_premium()
# ---------------------------------------------------------------------------

class TestPremium:
    def test_90_per_hour_outside_is_premium(self) -> None:
        """£90/hr × 8 = £720/day ≥ £700 → premium."""
        listing = _make_listing(day_rate_min=90, rate_period="hour", ir35_status="outside")
        assert is_premium(listing) is True

    def test_85_per_hour_outside_is_not_premium(self) -> None:
        """£85/hr × 8 = £680/day < £700 → not premium."""
        listing = _make_listing(day_rate_min=85, rate_period="hour", ir35_status="outside")
        assert is_premium(listing) is False

    def test_700_per_day_outside_is_premium(self) -> None:
        listing = _make_listing(day_rate_min=700, rate_period="day", ir35_status="outside")
        assert is_premium(listing) is True

    def test_700_per_day_inside_is_not_premium(self) -> None:
        listing = _make_listing(day_rate_min=700, rate_period="day", ir35_status="inside")
        assert is_premium(listing) is False

    def test_87_50_per_hour_outside_is_premium_boundary(self) -> None:
        """£87.50/hr × 8 = £700/day exactly → premium."""
        listing = _make_listing(day_rate_min=87.5, rate_period="hour", ir35_status="outside")
        assert is_premium(listing) is True


# ---------------------------------------------------------------------------
# Sanity checks: suspicious low / high thresholds
# ---------------------------------------------------------------------------

class TestSanityRates:
    def test_15_per_hour_is_suspicious_low(self) -> None:
        """£15/hr × 8 = £120/day ≤ £250 → should be flagged."""
        listing = _make_listing(day_rate_min=15, rate_period="hour")
        reasons = get_sanity_reasons(listing)
        assert any("suspiciously low" in r for r in reasons), (
            f"No low-rate flag found in: {reasons}"
        )

    def test_40_per_hour_not_suspicious_low(self) -> None:
        """£40/hr × 8 = £320/day > £250 → should NOT be flagged as suspiciously low."""
        listing = _make_listing(day_rate_min=40, rate_period="hour")
        reasons = get_sanity_reasons(listing)
        assert not any("suspiciously low" in r for r in reasons), (
            f"Unexpected low-rate flag in: {reasons}"
        )

    def test_daily_200_is_suspicious_low(self) -> None:
        """£200/day ≤ £250 → should still be flagged (regression guard)."""
        listing = _make_listing(day_rate_min=200, rate_period="day")
        reasons = get_sanity_reasons(listing)
        assert any("suspiciously low" in r for r in reasons)

    def test_200_per_hour_is_suspicious_high(self) -> None:
        """£200/hr × 8 = £1600/day ≥ £1500 → should be flagged."""
        listing = _make_listing(day_rate_min=200, rate_period="hour")
        reasons = get_sanity_reasons(listing)
        assert any("unusually high" in r for r in reasons), (
            f"No high-rate flag found in: {reasons}"
        )


# ---------------------------------------------------------------------------
# Seniority classification: _classify_seniority()
# ---------------------------------------------------------------------------

class TestClassifySeniority:
    def test_hourly_46_classified_as_junior(self) -> None:
        """£46/hr = £368/day equiv — junior tier (<480)."""
        listing = _make_listing(day_rate_min=46, rate_period="hour")
        assert _classify_seniority(listing) == "junior"

    def test_hourly_65_classified_as_mid(self) -> None:
        """£65/hr × 8 = £520/day — mid tier (480–700)."""
        listing = _make_listing(day_rate_min=65, rate_period="hour")
        assert _classify_seniority(listing) == "mid"

    def test_hourly_90_classified_as_senior(self) -> None:
        """£90/hr × 8 = £720/day — senior tier (≥700)."""
        listing = _make_listing(day_rate_min=90, rate_period="hour")
        assert _classify_seniority(listing) == "senior"

    def test_daily_600_classified_as_mid(self) -> None:
        listing = _make_listing(day_rate_min=600, rate_period="day")
        assert _classify_seniority(listing) == "mid"


# ---------------------------------------------------------------------------
# Sort order: effective_day_rate comparison
# ---------------------------------------------------------------------------

class TestSortOrder:
    def test_600_per_day_sorts_above_45_per_hour(self) -> None:
        """£600/day must sort above £45/hr (= £360/day equiv)."""
        l_day = _make_listing(day_rate_min=600, rate_period="day")
        l_hour = _make_listing(day_rate_min=45, rate_period="hour")
        rate_day = effective_day_rate(l_day) or 0
        rate_hour = effective_day_rate(l_hour) or 0
        assert rate_day > rate_hour, (
            f"£600/day ({rate_day}) should exceed £45/hr ({rate_hour} day-equiv)"
        )

    def test_500_per_day_sorts_below_70_per_hour(self) -> None:
        """£70/hr × 8 = £560/day should sort above £500/day."""
        l_day = _make_listing(day_rate_min=500, rate_period="day")
        l_hour = _make_listing(day_rate_min=70, rate_period="hour")
        rate_day = effective_day_rate(l_day) or 0
        rate_hour = effective_day_rate(l_hour) or 0
        assert rate_hour > rate_day, (
            f"£70/hr ({rate_hour} day-equiv) should exceed £500/day ({rate_day})"
        )
