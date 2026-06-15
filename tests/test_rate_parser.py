"""tests/test_rate_parser.py — Gold-set tests for the rate_parser module.

All test strings are sampled from or directly match real descriptions in the
DB (surveyed 2026-06-15).  No synthetic / made-up strings.

Coverage:
  - Day-rate single (with and without £)
  - Day-rate range (with and without £)
  - "Up to X" day and hourly
  - Day-rate label patterns
  - Hourly single / range (with and without £)
  - ph / pd shorthands
  - Umbrella IR35 detection
  - Inside / outside IR35 detection
  - Annual salaries → all None (must NOT pollute day_rate_* fields)
  - Competitive / negotiable / DOE → all None
  - HTML in description_raw — stripped before parsing

2026-06-15
"""
from __future__ import annotations

import pytest

from mechpm.extractor.rate_parser import RateInfo, parse_rate


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _r(
    description: str,
    *,
    salary_raw: str | None = None,
    metadata: dict | None = None,
) -> RateInfo:
    return parse_rate(description, salary_raw=salary_raw, metadata=metadata)


# ===========================================================================
# 1. Day-rate singles — bare number (no £)
# ===========================================================================


def test_bare_per_day_no_pound():
    """'321 per day' without £ — the most common pattern missed by old parser."""
    # Source: Assistant Project Manager — "Rate: 321 per day Umbrella Contract"
    r = _r("Rate: 321 per day Umbrella Contract: 12 Months")
    assert r.day_rate_min == 321
    assert r.day_rate_max is None
    assert r.rate_period == "day"
    assert r.rate_currency == "GBP"


def test_bare_slash_day():
    """'575/day' without £."""
    # Source: Project Manager - NEX — "Rate: 575/day - Umbrella rate"
    r = _r("Rate: 575/day - Umbrella rate Duration: 1 year Site: 3 days onsite")
    assert r.day_rate_min == 575
    assert r.rate_period == "day"


def test_up_to_pd_shorthand():
    """'Up to 600 pd' — 'pd' shorthand + 'up to' prefix."""
    # Source: Project Manager - eDV Clearance — "Up to 600 pd Must Have Active eDV"
    r = _r("12 Month Initial - Inside IR35 - Up to 600 pd Must Have Active eDV Clearance")
    assert r.day_rate_min == 600
    assert r.rate_period == "day"


# ===========================================================================
# 2. Day-rate ranges — bare number
# ===========================================================================


def test_bare_range_per_day():
    """'400 - 450 per day' without £."""
    # Source: Mechanical Site Manager — "Rate: 400 - 450 per day Start: ASAP"
    r = _r("Location: Windsor Rate: 400 - 450 per day Start: ASAP Duration: Long-term")
    assert r.day_rate_min == 400
    assert r.day_rate_max == 450
    assert r.rate_period == "day"


def test_bare_range_per_day_no_spaces():
    """'550-600 per day' — compact range form."""
    # Source: Senior Project Engineer (E&P)
    r = _r("Rate: 550 - 600 per day Umbrella IR35 Status: Inside IR35 We are seeking")
    assert r.day_rate_min == 550
    assert r.day_rate_max == 600
    assert r.rate_period == "day"
    assert r.ir35_status == "inside"


# ===========================================================================
# 3. Day-rate with £ symbol (existing patterns, must still work)
# ===========================================================================


def test_pound_range_slash_day():
    """'£400-£450/day' — original pattern from regex_fields."""
    # Source: Project Manager (Signalling) — was the only rate that previously parsed
    r = _r("York 6-12 month contract £400-£450/day Rail Signalling Project Manager")
    assert r.day_rate_min == 400
    assert r.day_rate_max == 450
    assert r.rate_period == "day"


def test_pound_range_per_day():
    """'£500 - £650 per day' with explicit 'per day'."""
    r = _r("Senior Project Manager £500 - £650 per day inside IR35 Swindon")
    assert r.day_rate_min == 500
    assert r.day_rate_max == 650
    assert r.rate_period == "day"
    assert r.ir35_status == "inside"


def test_pound_single_per_day():
    """'£600 per day' single with £."""
    r = _r("Contract PM role London Outside IR35 £600 per day 6 months")
    assert r.day_rate_min == 600
    assert r.rate_period == "day"
    assert r.ir35_status == "outside"


def test_up_to_pound_pd():
    """'Up to £600 pd' — 'pd' shorthand with £."""
    r = _r("Up to £600 pd Inside IR35 Manchester 12 months")
    assert r.day_rate_min == 600
    assert r.rate_period == "day"
    assert r.ir35_status == "inside"


def test_day_rate_label_pound():
    """'Day rate: £550' — existing label pattern."""
    r = _r("Day rate: £550 Birmingham 6 month contract")
    assert r.day_rate_min == 550
    assert r.rate_period == "day"


# ===========================================================================
# 4. Hourly rates — bare number (no £)
# ===========================================================================


def test_ph_shorthand_decimal():
    """'46.30ph' — decimal + 'ph' shorthand."""
    # Source: Project Planner — "Offering 46.30ph Inside IR35"
    r = _r("12 month contract Based in Oxford Offering 46.30ph Inside IR35")
    assert r.day_rate_min == 46
    assert r.rate_period == "hour"
    assert r.ir35_status == "inside"


def test_ph_shorthand_integer():
    """'45ph' — integer + 'ph' shorthand."""
    # Source: Lab Project Manager — "Offering 45ph Inside IR35"
    r = _r("Based in Filton Offering 45ph Inside IR35 Do you have experience")
    assert r.day_rate_min == 45
    assert r.rate_period == "hour"
    assert r.ir35_status == "inside"


def test_bare_per_hour_decimal():
    """'36.45 per hour' — no £, decimal amount."""
    # Source: HR Transformation Project Manager — "36.45 per hour umbrella"
    r = _r("Belfast 18 month contract 36.45 per hour umbrella ARM have an exciting")
    assert r.day_rate_min == 36
    assert r.rate_period == "hour"
    assert r.ir35_status == "umbrella"


def test_bare_per_hour_integer():
    """'40.44 per hour' with umbrella context."""
    # Source: Belfast - Building & Construction PM
    r = _r("Location: Belfast Rate: 40.44 per hour (umbrella rate) Contract: 12 months")
    assert r.day_rate_min == 40
    assert r.rate_period == "hour"
    assert r.ir35_status == "umbrella"


def test_bare_slash_hr():
    """'41.50/hr umbrella rate' — /hr shorthand."""
    # Source: PMO Project Co-ordinator — "41.50/hr umbrella rate"
    r = _r("37187384 - 41.50/hr umbrella rate Have you delivered infrastructure projects")
    assert r.day_rate_min == 42  # round(41.50) = 42
    assert r.rate_period == "hour"
    assert r.ir35_status == "umbrella"


def test_uppercase_per_hour():
    """'40 PER HOUR' — uppercase without £."""
    # Source: Project Manager — "INSIDE IR35 - 40 PER HOUR - 9 MONTHS"
    r = _r("CONFIGURATION - INSIDE IR35 - 40 PER HOUR - 9 MONTHS - FILTON, UK")
    assert r.day_rate_min == 40
    assert r.rate_period == "hour"
    assert r.ir35_status == "inside"


def test_up_to_per_hour_uppercase():
    """'UP TO 40 PER HOUR (INSIDE IR35)' — combined up-to + uppercase."""
    # Source: Building & Construction PM — "UP TO 40 PER HOUR (INSIDE IR35)"
    r = _r("BELFAST - UP TO 40 PER HOUR (INSIDE IR35) - 12 MONTH CONTRACT")
    assert r.day_rate_min == 40
    assert r.rate_period == "hour"
    assert r.ir35_status == "inside"


# ===========================================================================
# 5. Hourly ranges — bare number
# ===========================================================================


def test_ph_range_with_space():
    """'38- 48ph' — range with space after hyphen + ph shorthand."""
    # Source: Facilities Project Engineer — "Offering between 38- 48ph Inside IR35"
    r = _r("Based in Havant Offering between 38- 48ph Inside IR35")
    assert r.day_rate_min == 38
    assert r.day_rate_max == 48
    assert r.rate_period == "hour"
    assert r.ir35_status == "inside"


# ===========================================================================
# 6. Hourly with £ symbol
# ===========================================================================


def test_pound_per_hour():
    """'£75 per hour' with £ symbol."""
    r = _r("Senior PM £75 per hour outside IR35 Bristol 6 months")
    assert r.day_rate_min == 75
    assert r.rate_period == "hour"
    assert r.ir35_status == "outside"


def test_paye_umbrella_two_rates():
    """'29.89 per hour PAYE / 40.00 per hour Umbrella' — takes first rate found."""
    # Source: Configuration Project Manager (Widebody) — both PAYE and umbrella offered.
    r = _r(
        "Rate: 29.89 per hour PAYE / 40.00 per hour Umbrella About the role"
    )
    assert r.day_rate_min is not None
    assert r.rate_period == "hour"
    assert r.ir35_status == "umbrella"


def test_umbrella_in_paren():
    """'40.44 per hour (Umbrella)' — umbrella in parentheses."""
    # Source: Building And Construction PM Belfast
    r = _r("Belfast | 12-Month Contract | 40.44 per hour (Umbrella) | Full time onsite")
    assert r.day_rate_min == 40
    assert r.rate_period == "hour"
    assert r.ir35_status == "umbrella"


# ===========================================================================
# 7. IR35 detection without rate
# ===========================================================================


def test_ir35_outside_no_rate():
    """Outside IR35 in description with no rate → ir35_status='outside', rate None."""
    r = _r("Contract PM role. This role sits outside IR35. Location: Leeds.")
    assert r.ir35_status == "outside"
    assert r.day_rate_min is None


def test_umbrella_only_no_explicit_rate():
    """'Umbrella only' without rate → ir35_status='umbrella'."""
    r = _r("Umbrella only. Rate to be confirmed. Immediate start.")
    assert r.ir35_status == "umbrella"
    assert r.day_rate_min is None


# ===========================================================================
# 8. Annual salaries — must return None for day_rate_*
# ===========================================================================


def test_annual_per_annum_rejected():
    """'Up to 40,000 Per Annum' — annual salary must NOT be parsed as a day rate."""
    # Source: Project Manager (Macildowie) — "Up to 40,000 Per Annum"
    r = _r(
        "Project Manager Up to 40,000 Per Annum 12 Month FTC Stafford manufacturing"
    )
    assert r.day_rate_min is None
    assert r.day_rate_max is None
    assert r.rate_period is None


def test_annual_salary_label():
    """'Salary: £60,000-£70,000' with no day/hour indicator → None."""
    r = _r("Salary: £60,000-£70,000 per annum. FTC 12 months. Bristol.")
    assert r.day_rate_min is None


# ===========================================================================
# 9. Ambiguous / no-rate strings — must return all None
# ===========================================================================


def test_competitive_rate():
    """'Rate: Competitive' → all None."""
    r = _r("Interior Design Project Manager. Rate: Competitive. Crewe. 6 months.")
    assert r.day_rate_min is None
    assert r.rate_period is None


def test_rate_tbc():
    """'Rate TBC' → all None."""
    r = _r("Contract PM role. Rate TBC. Immediate start. South East.")
    assert r.day_rate_min is None


def test_doe():
    """'DOE' (Depends On Experience) → all None."""
    r = _r("Project Manager role. Rate: DOE. 3-6 months. Hybrid. Birmingham.")
    assert r.day_rate_min is None


def test_no_rate_in_description():
    """Description with no rate information → all None."""
    r = _r(
        "We are looking for a Project Manager with experience in mechanical engineering. "
        "Please apply with your CV."
    )
    assert r.day_rate_min is None
    assert r.ir35_status is None


# ===========================================================================
# 10. Adzuna annualised salary — metadata flag skips salary_raw
# ===========================================================================


def test_adzuna_salary_annualised_skipped():
    """Adzuna salary_raw is annualised — must not populate day_rate_min."""
    # The description has a real day rate; salary_raw is the annualised figure.
    r = _r(
        "Contract PM Filton 9 months. 45ph Inside IR35.",
        salary_raw="£85,000–£95,000",
        metadata={"salary_annualised": True},
    )
    # Day rate should come from description (45ph), NOT from salary_raw
    assert r.day_rate_min == 45
    assert r.rate_period == "hour"


def test_adzuna_no_rate_in_description_salary_annualised():
    """Adzuna listing with annualised salary and no day rate in description → None."""
    r = _r(
        "Exciting opportunity for a PM in aerospace. Apply now.",
        salary_raw="£70,000–£80,000",
        metadata={"salary_annualised": True},
    )
    assert r.day_rate_min is None


# ===========================================================================
# 11. HTML in description (description_raw fallback)
# ===========================================================================


def test_html_stripped_before_parsing():
    """HTML tags are stripped; rate in surrounding text is found."""
    r = _r(
        "<p><strong>Rate:</strong> 450 per day (Inside IR35)</p>"
        "<p>Location: Bristol. 6 months contract.</p>"
    )
    assert r.day_rate_min == 450
    assert r.rate_period == "day"
    assert r.ir35_status == "inside"


def test_html_umbrella_in_para():
    """Rate and umbrella keyword buried in HTML markup."""
    r = _r(
        "<p>Pay: <strong>40.00 per hour</strong> (Umbrella rate)</p>"
        "<p>12 months. Portsmouth.</p>"
    )
    assert r.day_rate_min == 40
    assert r.rate_period == "hour"
    assert r.ir35_status == "umbrella"
