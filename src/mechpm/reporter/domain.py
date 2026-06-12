"""UK contract-market reference data for mechanical engineering PM roles.

All figures are Polly's domain research, last reviewed 2026-06-12.  Update
rate bands and multipliers here when the market shifts — the renderer and
grouping logic pick them up automatically.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mechpm.models import NormalizedListing

# ---------------------------------------------------------------------------
# Rate bands by seniority (£/day, inclusive).
#
# Based on: Gatenby Sanderson, Kforce, Apex market surveys; cross-checked
# against Reed / CWJobs active listings as of June 2026.
# ---------------------------------------------------------------------------
RATE_BANDS_BY_SENIORITY: dict[str, tuple[int, int]] = {
    "junior":    (350,  600),
    "mid":       (480,  750),
    "senior":    (700,  950),
    "programme": (900, 1500),
}

# ---------------------------------------------------------------------------
# Regional pay multipliers relative to Midlands baseline (= 1.00).
#
# Apply to day_rate_max when comparing a listing's rate against Midlands bands.
# Inverse-apply to normalise a quoted rate before band classification.
# ---------------------------------------------------------------------------
REGION_PAY_MULTIPLIERS: dict[str, float] = {
    "London":     1.20,   # +20 % vs Midlands
    "South-East": 1.10,   # +10 %
    "Midlands":   1.00,   # baseline
    "North":      0.90,   # −10 %
    "Scotland":   0.85,   # −15 %
    "Wales":      0.90,   # −10 %
    "Remote":     1.00,   # rate-neutral; applicant pool is national
    "Other":      1.00,
}

# ---------------------------------------------------------------------------
# Red-flag patterns: (compiled_regex_pattern, human_reason).
#
# Scanned against description_clean during sanity checking.
# ---------------------------------------------------------------------------
RED_FLAG_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)\bpermanent\b",               "Perm role possibly mis-labelled as contract"),
    (r"(?i)\bsalary\b",                  "Salary language in contract listing"),
    (r"(?i)\bgross\s+rate\b",            "Gross rate quoted — may be umbrella-inflated"),
    (r"(?i)\bumbrella[- ]only\b",        "Umbrella-only engagement — verify net rate"),
    (r"(?i)\bno\s+ir35\b",               "IR35 status not stated in body text"),
    (r"(?i)\bconfidential\s+rate\b",     "Rate not disclosed"),
    (r"(?i)\bcompetitive\s+rate\b",      "Rate vague ('competitive') — request exact figure"),
    (r"(?i)\bnegotiable\b",              "Rate listed as negotiable — low signal"),
    (r"(?i)\bday\s+rate\s+on\s+application\b", "Day rate hidden — request disclosure"),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _effective_rate(listing: "NormalizedListing") -> float | None:
    """Return best single rate estimate: prefer day_rate_max, fall back to min."""
    if listing.day_rate_max:
        return listing.day_rate_max
    return listing.day_rate_min


def _infer_seniority_from_rate(rate: float) -> str:
    """Map a normalised rate (Midlands-equivalent) to a seniority band label."""
    if rate >= 900:
        return "programme"
    if rate >= 700:
        return "senior"
    if rate >= 480:
        return "mid"
    return "junior"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rate_context(listing: "NormalizedListing") -> str:
    """Return a short market-context annotation for a listing card.

    Examples: ``(mid-band, London)``  ``(below typical, Scotland)``
    """
    from mechpm.reporter.grouping import resolve_region  # local import avoids circular dep

    rate = _effective_rate(listing)
    if rate is None:
        return "(rate unknown)"

    region = resolve_region(listing.location_normalized)
    multiplier = REGION_PAY_MULTIPLIERS.get(region, 1.00)

    # Normalise to Midlands-equivalent before comparing against universal bands.
    normalised = rate / multiplier
    band = _infer_seniority_from_rate(normalised)
    lo, hi = RATE_BANDS_BY_SENIORITY[band]

    if normalised < lo * 0.85:
        position = "below typical"
    elif normalised > hi * 1.10:
        position = "above typical"
    else:
        position = f"{band}-band"

    return f"({position}, {region})"


def scan_red_flags(text: str) -> list[str]:
    """Return list of red-flag reason strings found in *text*."""
    reasons: list[str] = []
    for pattern, reason in RED_FLAG_PATTERNS:
        if re.search(pattern, text):
            reasons.append(reason)
    return reasons
