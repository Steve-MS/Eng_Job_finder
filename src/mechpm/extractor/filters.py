"""The four mandatory filters for NormalizedListing.

Precision-first design (Arthur's gates):
  contract filter  ≥ 98 % precision
  UK filter        ≥ 99 % precision
  PM role filter   ≥ 92 % precision
  mech domain      ≥ 96 % precision

Conservative defaults: when evidence is ambiguous the listing is REJECTED
(false negative accepted over false positive).  Recall is a secondary concern.

2026-06-12
"""
from __future__ import annotations

import re

from mechpm.models import NormalizedListing

# ---------------------------------------------------------------------------
# PM role detection
# ---------------------------------------------------------------------------

# Title regex — explicit PM / programme-manager titles.
# High precision: only match well-known role names, not "manager" alone.
# example: "Senior Project Manager"               → match
# example: "Programme Manager (Contract)"         → match
# example: "Project Management Officer"           → match
# example: "Delivery Manager, Rail"               → match
# example: "Engineering Manager"                  → NO match (too generic)
# example: "Site Manager"                         → NO match
PM_TITLE_RE = re.compile(
    r"\b(?:"
    r"project\s+manager|programme\s+manager|program(?:me)?\s+manager"
    r"|project\s+management\s+(?:consultant|lead|officer|specialist|professional|coordinator)"
    r"|delivery\s+manager|project\s+director|project\s+lead"
    r"|p\.?m\.?(?=[\s,\-]|$)"  # standalone "PM" abbreviation (conservative)
    r")\b",
    re.IGNORECASE,
)

# Body signals — count distinct hits; ≥2 qualifies when title match fails.
# Keep to well-defined PM artefacts; avoid generic words like "plan" or "risk".
PM_BODY_SIGNALS: list[str] = [
    "gantt",
    "raid log",
    "raid register",
    "raci matrix",
    "raci",
    "risk register",
    "project plan",
    "milestone",
    "workstream",
    "work stream",
    "change control",
    "stakeholder management",
    "work breakdown structure",
    "wbs",
    "change request",
    "lessons learned",
    "project governance",
    "critical path",
    "earned value",
    "project charter",
    "project lifecycle",
    "pmo",
    "project board",
    "project schedule",
]

# ---------------------------------------------------------------------------
# Mechanical engineering detection
# ---------------------------------------------------------------------------

# Keywords that score positively for mechanical domain.
# example: "HVAC design experience" → mech_score += 1
# example: "Rolling stock PM"       → mech_score += 3 (title)
MECH_KEYWORDS: list[str] = [
    "mechanical",
    "hvac",
    "heating",
    "ventilation",
    "air conditioning",
    "m&e",
    "building services",
    "piping",
    "pipework",
    "pressure vessel",
    "rotating equipment",
    "turbine",
    "compressor",
    "pump",
    "heat exchanger",
    "boiler",
    "thermal",
    "fluid",
    "process plant",
    "chemical plant",
    "refinery",
    "petrochemical",
    "oil and gas",
    "offshore",
    "nuclear",
    "rolling stock",
    "rail",
    "railway",
    "locomotive",
    "aerospace",
    "airframe",
    "avionics",
    "marine",
    "naval",
    "shipbuilding",
    "construction",
    "energy",
    "power station",
    "manufacturing",
    "automotive",
    "powertrain",
    "combustion",
    "structural steel",
]

# Phrases that disqualify — indicate a non-mechanical-engineering domain.
# Matched as substrings (no word boundary needed — phrases are specific enough).
# example: "software engineer"  → disqualify_score += 5 (in title)
# example: "IT project manager" → disqualify_score += 5 (in title)
DISQUALIFY_PHRASES: list[str] = [
    "software engineer",
    "software developer",
    "software development",
    "web developer",
    "web development",
    "mobile developer",
    "application developer",
    "app developer",
    "java developer",
    "python developer",
    ".net developer",
    "c# developer",
    "it project manager",
    "it programme manager",
    "erp project manager",
    "sap project manager",
    "data engineer",
    "cloud engineer",
    "network engineer",
    "devops engineer",
    "full stack developer",
    "front end developer",
    "back end developer",
    "civil engineer",      # disqualifies civil-only, not construction PM
    "electrical engineer", # disqualifies pure electrical, not M&E PM
]

# Sectors from vertical boards implicitly qualify (assigned by sector.py).
_MECHANICAL_SECTORS = frozenset({
    "rail",
    "aerospace",
    "defence",
    "energy",
    "nuclear",
    "construction",
    "maritime",
    "automotive",
    "process",
})


# ---------------------------------------------------------------------------
# Four filters
# ---------------------------------------------------------------------------

def passes_contract(listing: NormalizedListing) -> bool:
    """True only for contract roles (excludes perm / contract-to-perm).

    Conservative: once a listing is tagged 'perm' it never passes, even if
    contract language also appears.  Adapters pre-filter by contract type so
    ambiguous defaults to 'contract' (see regex_fields.parse_contract_type).
    """
    return listing.contract_type == "contract"


def passes_uk(listing: NormalizedListing) -> bool:
    """True only for listings based in the UK (country == 'GB').

    Conservative: a non-GB country code always fails regardless of any other
    indicator.  UK-specific boards default to GB when location is missing.
    """
    return listing.country == "GB"


def passes_pm_role(listing: NormalizedListing) -> bool:
    """True when the listing is clearly for a PM / project-management role.

    Primary: explicit PM title keywords (high precision, low recall).
    Fallback: ≥ 2 distinct PM body-signal phrases in the description.
    """
    if PM_TITLE_RE.search(listing.title or ""):
        return True

    desc = " ".join(filter(None, [listing.description_raw, listing.description_clean]))
    if not desc.strip():
        return False

    desc_lower = desc.lower()
    hit_count = sum(1 for signal in PM_BODY_SIGNALS if signal in desc_lower)
    return hit_count >= 2


def passes_mechanical(listing: NormalizedListing) -> bool:
    """True when the role is in a mechanical-engineering domain.

    For vertical-board sectors (rail, aerospace, defence, energy, nuclear,
    construction, maritime, automotive, process): pre-qualified unless the
    title explicitly belongs to an IT/software domain.

    For 'generalist' sector: requires mech_score ≥ 1 AND mech_score > disqualify_score,
    with title hits weighted more heavily than description hits.
    """
    if listing.sector in _MECHANICAL_SECTORS:
        title_lower = (listing.title or "").lower()
        if any(phrase in title_lower for phrase in DISQUALIFY_PHRASES):
            return False
        return True

    # Generalist sector: keyword scoring
    title = (listing.title or "").lower()
    desc = (listing.description_raw or "").lower()

    mech_score = (
        3 * sum(1 for kw in MECH_KEYWORDS if re.search(r"\b" + re.escape(kw) + r"\b", title))
        + sum(1 for kw in MECH_KEYWORDS if re.search(r"\b" + re.escape(kw) + r"\b", desc))
    )
    disqualify_score = (
        5 * sum(1 for ph in DISQUALIFY_PHRASES if ph in title)
        + 2 * sum(1 for ph in DISQUALIFY_PHRASES if ph in desc)
    )
    return mech_score >= 1 and mech_score > disqualify_score


def passes_all(listing: NormalizedListing) -> tuple[bool, list[str]]:
    """Run all four filters; return (all_passed, list_of_failed_filter_names).

    The failure list is empty when the listing passes every gate.
    Arthur's test harness can assert which specific gate rejected a listing.
    """
    failures: list[str] = []
    if not passes_contract(listing):
        failures.append("contract")
    if not passes_uk(listing):
        failures.append("uk")
    if not passes_pm_role(listing):
        failures.append("pm_role")
    if not passes_mechanical(listing):
        failures.append("mechanical")
    return len(failures) == 0, failures
