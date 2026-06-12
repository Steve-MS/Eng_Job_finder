"""Tier-2 extraction: regex-based field parsing.

All public regex constants have at least one  # example: "..." → result  comment
so Arthur's test suite can verify intent without reverse-engineering the pattern.

Currency is always GBP (£) for UK contract boards.  Non-GBP boards are out
of scope for v0.1.

2026-06-12
"""
from __future__ import annotations

import re
from datetime import date
from typing import TypedDict

# ---------------------------------------------------------------------------
# Rate patterns
# ---------------------------------------------------------------------------

# Range rate: "£min [-/–/to] £max per day/hour"
# example: "£500 - £650 per day"  → min=500.0, max=650.0, period="day"
# example: "£300 – £400/hr"       → min=300.0, max=400.0, period="hour"
# example: "£450–550 per day"     → min=450.0, max=550.0, period="day"
# example: "£400 to £500 p/d"     → min=400.0, max=500.0, period="day"
RATE_RANGE_RE = re.compile(
    r"£\s*([\d,]+)\s*(?:-|–|to)\s*£?\s*([\d,]+)\s*"
    r"(?:per\s+|/\s*|p\.?\s*)?(day|d\b|hr\b|hour|p\/d|pd\b)",
    re.IGNORECASE,
)

# Single explicit rate: "£amount per day/hour"
# example: "£550/day"             → min=550.0, period="day"
# example: "£75 per hour"         → min=75.0, period="hour"
# example: "£600 p/d"             → min=600.0, period="day"
# example: "£600 pd"              → min=600.0, period="day"
RATE_SINGLE_RE = re.compile(
    r"£\s*([\d,]+)\s*(?:per\s+|/\s*|p[./]?\s*)(day|d\b|hr\b|hour)",
    re.IGNORECASE,
)

# Labelled day-rate line — "Day rate: £X" or "Daily rate £X–£Y"
# example: "Day rate: £600"           → min=600.0, period="day"
# example: "Daily rate £550 - £650"   → min=550.0, max=650.0, period="day"
RATE_LABEL_RE = re.compile(
    r"(?:day|daily)\s+rate\s*:?\s*£\s*([\d,]+)(?:\s*[-–]\s*£?\s*([\d,]+))?",
    re.IGNORECASE,
)

# IR35 status
# example: "Outside IR35"         → "outside"
# example: "Inside IR35 role"     → "inside"
# example: "Deemed inside IR35"   → "inside"
# example: "IR35 TBC"             → no match → caller returns "not_stated"
IR35_RE = re.compile(r"\b(inside|outside)\s+ir35\b", re.IGNORECASE)

# Duration
# example: "6 months"    → duration_weeks=26
# example: "3 mths"      → duration_weeks=13
# example: "12 weeks"    → duration_weeks=12
# example: "1 year"      → duration_weeks=52
# example: "2 yrs"       → duration_weeks=104
DURATION_RE = re.compile(
    r"\b(\d+)\s*(year|yr|month|mth|week|wk)s?\b",
    re.IGNORECASE,
)

# ASAP / immediate start
# example: "Start ASAP"            → asap_flag=True
# example: "Immediate start"       → asap_flag=True
# example: "Urgent requirement"    → asap_flag=True
# example: "June 2026 start"       → no match
ASAP_RE = re.compile(
    r"\b(asap|immediate(?:ly)?|urgent(?:\s+start)?)\b",
    re.IGNORECASE,
)

# Start date phrases (contextual — must follow a start-related keyword)
# example: "Starting June 2026"        → "June 2026"
# example: "Start date: 01/07/2026"    → "01/07/2026"
# example: "Available from Aug 2026"   → "Aug 2026"
# example: "Commencing 2026-09-01"     → "2026-09-01"
# example: "1st March 2026"            → "1st March 2026"
START_DATE_RE = re.compile(
    r"\b(?:start(?:s|ing|[\s\-]date)?|available\s+from|commence[sd]?)\s*:?\s*"
    r"("
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}"
    r"|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*(?:\d{4})?"
    r")",
    re.IGNORECASE,
)

# Contract type — positive contract signals
# example: "Contract role"                → "contract"
# example: "Fixed Term Contract (FTC)"    → "contract"
# example: "Freelance engagement"         → "contract"
# example: "Contract (outside IR35)"      → "contract"
CONTRACT_SIGNALS_RE = re.compile(
    r"\b(contract(?!\s*(?:to|2)\s*perm)|freelance|ftc|fixed[- ]term\s+contract)\b",
    re.IGNORECASE,
)

# Permanent / contract-to-perm signals
# example: "Permanent position"     → "perm"
# example: "Contract to Perm"       → "perm"
# example: "C2P opportunity"        → "perm"
# example: "Full-time permanent"    → "perm"
PERM_SIGNALS_RE = re.compile(
    r"\b(permanent|perm(?!\w)|contract\s+to\s+perm|c2p|full[- ]time\s+permanent)\b",
    re.IGNORECASE,
)

# Remote policy
# example: "Fully remote working"            → "remote"
# example: "Remote role, work from home"     → "remote"
# example: "WFH available"                   → "remote"
REMOTE_RE = re.compile(
    r"\b(fully\s+remote|remote\s+(?:working|only|role|position)|"
    r"work(?:ing)?\s+from\s+home|wfh)\b",
    re.IGNORECASE,
)

# example: "Hybrid working (3 days WFH)"     → "hybrid"
# example: "2 days in office"                → "hybrid"
# example: "Part-remote"                     → "hybrid"
HYBRID_RE = re.compile(
    r"\b(hybrid(?:\s+working)?|\d+\s+days?\s+(?:remote|wfh|in\s+office)|"
    r"part[- ]remote)\b",
    re.IGNORECASE,
)

# example: "Office-based role"               → "on-site"
# example: "On-site only"                    → "on-site"
# example: "No remote working"               → "on-site"
ONSITE_RE = re.compile(
    r"\b(on[- ]?site(?:\s+only)?|office[- ]?based|no\s+remote|site[- ]?based)\b",
    re.IGNORECASE,
)

# UK postcode pattern — used to strip postcodes from location strings
# example: "Manchester M1 4BT" → strip "M1 4BT" → "Manchester"
# example: "London EC2V 8RF"   → strip "EC2V 8RF" → "London"
UK_POSTCODE_RE = re.compile(
    r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Month name → number lookup (used by parse_start_date)
# ---------------------------------------------------------------------------
_MONTH_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Non-UK location patterns (for country detection)
# example: "Dublin, Ireland"     → country="IE"
# example: "Amsterdam, NL"       → country="NL"
_NON_UK_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(dublin|ireland|republic\s+of\s+ireland)\b", re.IGNORECASE), "IE"),
    (re.compile(r"\b(netherlands|amsterdam|rotterdam)\b", re.IGNORECASE), "NL"),
    (re.compile(r"\b(germany|berlin|munich|hamburg|frankfurt)\b", re.IGNORECASE), "DE"),
    (re.compile(r"\b(france|paris|lyon|marseille)\b", re.IGNORECASE), "FR"),
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class RateResult(TypedDict, total=False):
    day_rate_min: float
    day_rate_max: float
    rate_currency: str
    rate_period: str


# ---------------------------------------------------------------------------
# Public extraction functions
# ---------------------------------------------------------------------------

def parse_rate(salary_raw: str | None, description: str | None = None) -> RateResult:
    """Extract min/max rate, currency, and period from salary_raw then description.

    Tries RATE_RANGE_RE → RATE_LABEL_RE → RATE_SINGLE_RE in priority order.
    Falls back to scanning description_raw when salary_raw yields nothing.
    """
    result: RateResult = {}
    texts = [t for t in [salary_raw, description] if t]
    for text in texts:
        # Range pattern (highest specificity)
        m = RATE_RANGE_RE.search(text)
        if m:
            result["day_rate_min"] = _parse_amount(m.group(1))
            result["day_rate_max"] = _parse_amount(m.group(2))
            result["rate_period"] = _normalise_period(m.group(3) or "day")
            result["rate_currency"] = "GBP"
            return result

        # Day-rate label (may contain a range)
        m = RATE_LABEL_RE.search(text)
        if m:
            result["day_rate_min"] = _parse_amount(m.group(1))
            if m.group(2):
                result["day_rate_max"] = _parse_amount(m.group(2))
            result["rate_period"] = "day"
            result["rate_currency"] = "GBP"
            return result

        # Single value
        m = RATE_SINGLE_RE.search(text)
        if m:
            result["day_rate_min"] = _parse_amount(m.group(1))
            result["rate_period"] = _normalise_period(m.group(2) or "day")
            result["rate_currency"] = "GBP"
            return result

    return result


def parse_ir35(text: str | None) -> str | None:
    """Return 'inside', 'outside', or None (caller maps None → "not_stated").

    # example: "Outside IR35"  → "outside"
    # example: "Inside IR35"   → "inside"
    # example: "IR35 applies"  → None   (ambiguous)
    # example: None            → None
    """
    if not text:
        return None
    m = IR35_RE.search(text)
    return m.group(1).lower() if m else None


def parse_duration(text: str | None) -> tuple[str | None, int | None]:
    """Return (raw_phrase, weeks) from a duration string.

    # example: "6 months"  → ("6 months", 26)
    # example: "12 weeks"  → ("12 weeks", 12)
    # example: "1 year"    → ("1 year",   52)
    # example: "2 yrs"     → ("2 yrs",   104)
    # example: None        → (None, None)
    """
    if not text:
        return None, None
    m = DURATION_RE.search(text)
    if not m:
        return None, None
    value = int(m.group(1))
    unit = m.group(2).lower()
    raw_phrase = m.group(0)
    if unit in ("year", "yr"):
        return raw_phrase, value * 52
    if unit in ("month", "mth"):
        return raw_phrase, value * 4
    if unit in ("week", "wk"):
        return raw_phrase, value
    return raw_phrase, None


def parse_asap(text: str | None) -> bool:
    """Return True if text contains an ASAP/immediate/urgent start signal.

    # example: "Start ASAP"      → True
    # example: "Immediate start" → True
    # example: "June 2026 start" → False
    # example: None              → False
    """
    if not text:
        return False
    return bool(ASAP_RE.search(text))


def parse_start_date_raw(text: str | None) -> str | None:
    """Extract the raw start-date phrase from text.

    # example: "Starting June 2026"         → "June 2026"
    # example: "Start date: 01/07/2026"     → "01/07/2026"
    # example: "Available from Aug 2026"    → "Aug 2026"
    # example: "No start date mentioned"    → None
    """
    if not text:
        return None
    m = START_DATE_RE.search(text)
    return m.group(1).strip() if m else None


def parse_start_date(raw: str | None) -> date | None:
    """Parse a raw start-date string to a Python date.

    # example: "June 2026"     → date(2026, 6, 1)
    # example: "01/07/2026"    → date(2026, 7, 1)
    # example: "2026-09-01"    → date(2026, 9, 1)
    # example: "1st March 26"  → date(2026, 3, 1)
    # example: "asap"          → None  (handled by asap_flag)
    """
    if not raw:
        return None
    text = raw.strip().lower()
    # ISO: 2026-09-01
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # "June 2026" or "Jun 2026"
    m = re.search(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})\b",
        text,
    )
    if m:
        return date(int(m.group(2)), _MONTH_MAP[m.group(1)[:3]], 1)
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.search(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def parse_contract_type(
    contract_type_raw: str | None,
    title: str = "",
    description: str = "",
) -> str:
    """Classify as 'contract' or 'perm'.

    Conservative: explicit perm signals always win; adapters pre-filter for
    contracts so ambiguous defaults to 'contract'.

    # example: "Contract"              → "contract"
    # example: "Permanent"             → "perm"
    # example: "Fixed Term Contract"   → "contract"
    # example: "Contract to Perm"      → "perm"
    # example: None                    → "contract"  (adapter searched contracts)
    """
    texts = " ".join(t for t in [contract_type_raw, title, description] if t)
    if PERM_SIGNALS_RE.search(texts):
        return "perm"
    if CONTRACT_SIGNALS_RE.search(texts):
        return "contract"
    return "contract"


def parse_remote_policy(text: str | None) -> str | None:
    """Return 'remote', 'hybrid', 'on-site', or None.

    Hybrid is checked before remote because patterns like "2 days WFH" contain
    WFH (a remote signal) inside a hybrid context.

    # example: "Fully remote working"          → "remote"
    # example: "Hybrid working, 2 days WFH"    → "hybrid"
    # example: "Office-based, no remote"       → "on-site"
    # example: "London-based"                  → None
    """
    if not text:
        return None
    # Check hybrid first — "2 days WFH" is hybrid, not remote
    if HYBRID_RE.search(text):
        return "hybrid"
    if REMOTE_RE.search(text):
        return "remote"
    if ONSITE_RE.search(text):
        return "on-site"
    return None


def normalize_location(raw: str | None) -> str:
    """Strip UK postcodes, collapse whitespace.  Returns "" on empty input.

    # example: "Manchester, M1 4BT"  → "Manchester"
    # example: "London EC2V 8RF"     → "London"
    # example: None                  → ""
    """
    if not raw:
        return ""
    loc = UK_POSTCODE_RE.sub("", raw)
    loc = re.sub(r"\s+", " ", loc).strip().strip(",").strip()
    return loc


def detect_country(location_raw: str | None) -> str:
    """Return ISO 3166-1 alpha-2 country code; defaults to 'GB'.

    # example: "Dublin, Ireland"  → "IE"
    # example: "Amsterdam"        → "NL"
    # example: "Leeds"            → "GB"
    # example: None               → "GB"
    """
    if not location_raw:
        return "GB"
    for pattern, code in _NON_UK_MAP:
        if pattern.search(location_raw):
            return code
    return "GB"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_amount(s: str) -> float:
    """Strip commas and convert to float.  example: "1,200" → 1200.0"""
    return float(s.replace(",", ""))


def _normalise_period(raw: str) -> str:
    """Normalise rate-period token to 'day' or 'hour'.

    example: "hr"    → "hour"
    example: "pd"    → "day"
    example: "d"     → "day"
    """
    r = raw.lower().strip()
    if r in ("hr", "hour", "hourly", "h"):
        return "hour"
    return "day"
