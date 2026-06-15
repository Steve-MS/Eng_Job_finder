"""Rate and IR35 parser for UK contract job listings.

The key improvement over regex_fields.parse_rate is supporting the dominant
real-world pattern on UK job boards: bare numbers without a £ symbol, paired
with explicit period indicators (per day, per hour, ph, pd, /day, /hr).

Pattern priority:
  1. Day-rate range with £  (e.g. £400-£450/day)
  2. Day-rate range bare    (e.g. 400 - 450 per day)
  3. Day-rate single with £ (e.g. £600 pd)
  4. Day-rate single bare   (e.g. 321 per day, 575/day)
  5. "Up to X" day rate     (e.g. Up to 600 pd)
  6. Day-rate label         (e.g. Day rate: £550, Rate: 400-450 per day)
  7. Hourly range with £    (e.g. £45-£55 per hour)
  8. Hourly range bare      (e.g. 38-48ph)
  9. Hourly single with £   (e.g. £75/hr)
 10. Hourly single bare     (e.g. 46.30ph, 36.45 per hour, 40 PER HOUR)
 11. "Up to X" hourly       (e.g. Up to 50 per hour)

Annual salaries (per annum / per year) are explicitly skipped.
Adzuna listings with salary_annualised=True in metadata skip salary_raw
entirely (Adzuna converts day rates to annualised figures before indexing).

2026-06-15
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Amount pattern
# ---------------------------------------------------------------------------

# Matches integers and decimals, with optional comma thousands separator.
# Examples: 321, 1,200, 46.30, 38, 40.44
_AMT = r'[\d]+(?:,\d{3})*(?:\.\d+)?'

# Lookahead: the token after the amount must NOT be an annual marker.
# This guards against "£60,000 per annum" matching as a day rate.
_NOT_ANNUAL = r'(?!\s*(?:per\s+annum|per\s+year|per\s+yr|,?\s*pa\b|annually))'

# ---------------------------------------------------------------------------
# Period-indicator sub-patterns
# ---------------------------------------------------------------------------

# Day-rate period indicators
_DAY_IND = r'(?:per\s+day\b|/\s*day\b|\bpd\b|p\s*/\s*d\b)'

# Hourly period indicators.
# NOTE: ph\b (no leading \b) is intentional — when ph immediately follows a
# digit (e.g. "46.30ph", "45ph") there is no \w→\W boundary before "p"
# because digits are \w characters.  The word boundary at the end (ph\b) is
# sufficient to prevent false matches inside longer words.
_HOUR_IND = r'(?:per\s+hour\b|per\s+hr\b|/\s*(?:hour|hr)\b|ph\b)'

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# -- Day rates ---------------------------------------------------------------

# Range with £:  £400 - £450 per day,  £400-£450/day
_RANGE_DAY_GBP = re.compile(
    r'£\s*(' + _AMT + r')\s*[-–]\s*£?\s*(' + _AMT + r')\s*' + _DAY_IND,
    re.IGNORECASE,
)

# Range bare:  400 - 450 per day,  550-600 per day
_RANGE_DAY_BARE = re.compile(
    r'\b(' + _AMT + r')\s*[-–]\s*(' + _AMT + r')\s+' + _DAY_IND,
    re.IGNORECASE,
)

# Single with £:  £600 pd,  £550/day,  £600 per day
_SINGLE_DAY_GBP = re.compile(
    r'£\s*(' + _AMT + r')\s*' + _DAY_IND,
    re.IGNORECASE,
)

# Single bare:  321 per day,  575/day
_SINGLE_DAY_BARE = re.compile(
    r'\b(' + _AMT + r')\s*' + _DAY_IND,
    re.IGNORECASE,
)

# Up to X day:  Up to 600 pd,  up to £550 per day
_UPTO_DAY = re.compile(
    r'\bup\s+to\s+£?\s*(' + _AMT + r')\s*' + _DAY_IND,
    re.IGNORECASE,
)

# Day-rate label (no explicit period suffix needed — label implies "per day"):
#   Day rate: £600,  Daily rate: £550 - £650
_DAYRATE_LABEL = re.compile(
    r'(?:day|daily)\s+rate\s*:?\s*£?\s*(' + _AMT + r')(?:\s*[-–]\s*£?\s*(' + _AMT + r'))?',
    re.IGNORECASE,
)

# Rate: label with explicit period:  Rate: 321 per day,  Rate: 400-450 per day
_RATE_LABEL_DAY = re.compile(
    r'\brate\s*:?\s*£?\s*(' + _AMT + r')(?:\s*[-–]\s*£?\s*(' + _AMT + r'))?\s*' + _DAY_IND,
    re.IGNORECASE,
)

# -- Hourly rates ------------------------------------------------------------

# Range with £:  £45 - £55 per hour
_RANGE_HOUR_GBP = re.compile(
    r'£\s*(' + _AMT + r')\s*[-–]\s*£?\s*(' + _AMT + r')\s*' + _HOUR_IND,
    re.IGNORECASE,
)

# Range bare:  38-48ph,  38- 48ph,  45 - 55 per hour
_RANGE_HOUR_BARE = re.compile(
    r'\b(' + _AMT + r')\s*[-–]\s*(' + _AMT + r')\s*' + _HOUR_IND,
    re.IGNORECASE,
)

# Single with £:  £75/hr,  £45 per hour
_SINGLE_HOUR_GBP = re.compile(
    r'£\s*(' + _AMT + r')\s*' + _HOUR_IND,
    re.IGNORECASE,
)

# Single bare:  46.30ph,  36.45 per hour,  40 PER HOUR,  41.50/hr
_SINGLE_HOUR_BARE = re.compile(
    r'\b(' + _AMT + r')\s*' + _HOUR_IND,
    re.IGNORECASE,
)

# Up to X hourly:  Up to 50 per hour,  UP TO 40 PER HOUR
_UPTO_HOUR = re.compile(
    r'\bup\s+to\s+£?\s*(' + _AMT + r')\s*' + _HOUR_IND,
    re.IGNORECASE,
)

# -- Annual salary blockers --------------------------------------------------

# These patterns signal that the amount is an annual salary, not a day rate.
# Checked against the match neighbourhood (40 chars after match end).
_ANNUAL_SUFFIX = re.compile(
    r'\bper\s+annum\b|\bper\s+year\b|\bper\s+yr\b|\bannually\b|\bpa\b',
    re.IGNORECASE,
)

# Also block amounts > 2 000 that appear after common annual salary labels.
_ANNUAL_LABEL = re.compile(
    r'\bsalary\s*[:=]?\s*£?\s*[\d,]+|\bper\s+annum\b|\bcompetitive\s+salary\b',
    re.IGNORECASE,
)

# -- IR35 / umbrella ---------------------------------------------------------

_IR35_EXPLICIT = re.compile(r'\b(inside|outside)\s+ir35\b', re.IGNORECASE)
_UMBRELLA_RE = re.compile(
    r'\bumbrella\b',
    re.IGNORECASE,
)

# HTML tag stripper (safety net for description_raw fallback)
_HTML_TAG = re.compile(r'<[^>]+>')


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


@dataclass
class RateInfo:
    """Structured rate extraction result."""

    day_rate_min: int | None = None
    day_rate_max: int | None = None
    rate_currency: str | None = None
    rate_period: str | None = None    # "day" | "hour"
    ir35_status: str | None = None    # "inside" | "outside" | "umbrella" | None
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_rate(
    description: str,
    salary_raw: str | None = None,
    metadata: dict | None = None,
) -> RateInfo:
    """Extract rate and IR35 status from job listing text.

    Args:
        description:  Cleaned or raw job description text (primary source).
        salary_raw:   Structured salary string from the adapter, if any.
                      Ignored when metadata["salary_annualised"] is True
                      (Adzuna's annualised salary is not a day rate).
        metadata:     Adapter metadata dict; checked for salary_annualised flag.

    Returns:
        RateInfo with fields populated where evidence is found.
        Conservative: leaves fields None when evidence is absent or ambiguous.
    """
    # Strip HTML from description (safety net when description_raw is used)
    clean = _HTML_TAG.sub(" ", description) if description else ""
    clean = re.sub(r"\s+", " ", clean).strip()

    # Adzuna annualised salary — skip salary_raw entirely; regex description only
    annualised = bool(metadata and metadata.get("salary_annualised"))
    effective_salary_raw = None if annualised else salary_raw

    # Build list of texts to scan: salary_raw first (higher specificity), then
    # description.  Both are scanned so description catches what salary_raw misses.
    texts_to_scan = [t for t in [effective_salary_raw, clean] if t]

    rate_info = RateInfo()

    for text in texts_to_scan:
        result = _try_extract_rate(text)
        if result is not None:
            rate_info.day_rate_min = result["min"]
            rate_info.day_rate_max = result.get("max")
            rate_info.rate_period = result["period"]
            rate_info.rate_currency = "GBP"
            rate_info.confidence = result["confidence"]
            break

    # IR35 / umbrella — scan full description regardless of rate success
    rate_info.ir35_status = _extract_ir35(clean)

    return rate_info


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _try_extract_rate(text: str) -> dict | None:
    """Apply patterns in priority order; return first match as a dict.

    Returns:
        Dict with keys: min (int), max (int | None), period (str), confidence (float).
        None when no pattern matches.
    """
    # Check whether the whole text is dominated by annual salary language.
    # If there's no day/hour indicator, trust the annual blocker.
    if _ANNUAL_LABEL.search(text) and not re.search(
        _DAY_IND + r"|" + _HOUR_IND, text, re.IGNORECASE
    ):
        return None

    # --- Day rates (higher confidence first) --------------------------------

    m = _RANGE_DAY_GBP.search(text)
    if m and not _is_annual(text, m.end()):
        return {
            "min": _to_int(m.group(1)),
            "max": _to_int(m.group(2)),
            "period": "day",
            "confidence": 0.95,
        }

    m = _RANGE_DAY_BARE.search(text)
    if m and not _is_annual(text, m.end()):
        min_val = _to_int(m.group(1))
        max_val = _to_int(m.group(2))
        if _is_plausible_day_rate(min_val) and _is_plausible_day_rate(max_val):
            return {
                "min": min_val,
                "max": max_val,
                "period": "day",
                "confidence": 0.85,
            }

    m = _SINGLE_DAY_GBP.search(text)
    if m and not _is_annual(text, m.end()):
        return {
            "min": _to_int(m.group(1)),
            "max": None,
            "period": "day",
            "confidence": 0.90,
        }

    m = _UPTO_DAY.search(text)
    if m and not _is_annual(text, m.end()):
        val = _to_int(m.group(1))
        if _is_plausible_day_rate(val):
            return {"min": val, "max": None, "period": "day", "confidence": 0.80}

    m = _RATE_LABEL_DAY.search(text)
    if m and not _is_annual(text, m.end()):
        min_val = _to_int(m.group(1))
        max_val = _to_int(m.group(2)) if m.group(2) else None
        if _is_plausible_day_rate(min_val):
            return {
                "min": min_val,
                "max": max_val,
                "period": "day",
                "confidence": 0.85,
            }

    m = _SINGLE_DAY_BARE.search(text)
    if m and not _is_annual(text, m.end()):
        val = _to_int(m.group(1))
        if _is_plausible_day_rate(val):
            return {"min": val, "max": None, "period": "day", "confidence": 0.80}

    m = _DAYRATE_LABEL.search(text)
    if m:
        min_val = _to_int(m.group(1))
        max_val = _to_int(m.group(2)) if m.group(2) else None
        if _is_plausible_day_rate(min_val):
            return {
                "min": min_val,
                "max": max_val,
                "period": "day",
                "confidence": 0.85,
            }

    # --- Hourly rates -------------------------------------------------------

    m = _RANGE_HOUR_GBP.search(text)
    if m:
        return {
            "min": _to_int(m.group(1)),
            "max": _to_int(m.group(2)),
            "period": "hour",
            "confidence": 0.95,
        }

    m = _RANGE_HOUR_BARE.search(text)
    if m:
        min_val = _to_int(m.group(1))
        max_val = _to_int(m.group(2))
        if _is_plausible_hourly_rate(min_val) and _is_plausible_hourly_rate(max_val):
            return {
                "min": min_val,
                "max": max_val,
                "period": "hour",
                "confidence": 0.85,
            }

    m = _SINGLE_HOUR_GBP.search(text)
    if m:
        return {
            "min": _to_int(m.group(1)),
            "max": None,
            "period": "hour",
            "confidence": 0.90,
        }

    m = _UPTO_HOUR.search(text)
    if m:
        val = _to_int(m.group(1))
        if _is_plausible_hourly_rate(val):
            return {"min": val, "max": None, "period": "hour", "confidence": 0.80}

    m = _SINGLE_HOUR_BARE.search(text)
    if m:
        val = _to_int(m.group(1))
        if _is_plausible_hourly_rate(val):
            return {"min": val, "max": None, "period": "hour", "confidence": 0.80}

    return None


def _extract_ir35(text: str) -> str | None:
    """Return 'inside', 'outside', 'umbrella', or None from description text."""
    if not text:
        return None
    m = _IR35_EXPLICIT.search(text)
    if m:
        return m.group(1).lower()
    if _UMBRELLA_RE.search(text):
        return "umbrella"
    return None


def _is_annual(text: str, match_end: int) -> bool:
    """Return True if the text within 40 chars after match_end has an annual marker."""
    snippet = text[match_end : match_end + 40]
    return bool(_ANNUAL_SUFFIX.search(snippet))


def _is_plausible_day_rate(value: int) -> bool:
    """UK contract day rates are realistically in the £100-£3000 range."""
    return 100 <= value <= 3000


def _is_plausible_hourly_rate(value: int) -> bool:
    """UK contract hourly rates are realistically in the £10-£250 range."""
    return 10 <= value <= 250


def _to_int(s: str) -> int:
    """Strip commas and convert to int (rounding from float).

    example: "46.30" → 46
    example: "1,200" → 1200
    example: "400"   → 400
    """
    return round(float(s.replace(",", "")))
