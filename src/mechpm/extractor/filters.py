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

from mechpm.extractor.regex_fields import _NON_UK_MAP
from mechpm.models import NormalizedListing

# Frozenset of every ISO code that _NON_UK_MAP can produce.
# Used by passes_uk to distinguish "definitely non-UK" from "unknown country".
_KNOWN_NON_UK_CODES: frozenset[str] = frozenset(code for _, code in _NON_UK_MAP)

# ---------------------------------------------------------------------------
# PM role detection
# ---------------------------------------------------------------------------

# Title regex — explicit PM / programme-manager titles.
# High precision: only match well-known role names, not "manager" alone.
# example: "Senior Project Manager"               → match
# example: "Programme Manager (Contract)"         → match
# example: "Project Management Officer"           → match
# example: "Delivery Manager, Rail"               → match
# example: "Commissioning Manager"                → match (A1 — Tommy v0.2)
# example: "M&E Manager"                          → match (A1 — Tommy v0.2)
# example: "Engineering Manager"                  → match (A1 — Tommy v0.2)
# example: "Site Manager (Mechanical)"            → match (A1 — Tommy v0.2)
PM_TITLE_RE = re.compile(
    r"\b(?:"
    # --- existing (unchanged) ---
    r"project\s+manager|programme\s+manager|program(?:me)?\s+manager"
    r"|project\s+management\s+(?:consultant|lead|officer|specialist|professional|coordinator)"
    r"|delivery\s+manager|project\s+director|project\s+lead"
    r"|p\.?m\.?(?=[\s,\-]|$)"  # standalone "PM" abbreviation (conservative)
    # --- NEW (A1): sector-specific PM titles common on Energy Jobline / Adzuna ---
    r"|engineering\s+manager"                 # "Engineering Manager (Contract)"
    r"|construction\s+manager"               # "Construction Manager — M&E"
    r"|site\s+manager"                       # "Site Manager (Mechanical)"
    r"|commissioning\s+manager"              # "Commissioning Manager"
    r"|m\s*&\s*e\s+manager"                  # "M&E Manager"
    r"|installations?\s+manager"             # "Installation Manager"
    r"|contracts?\s+manager"                 # "Contract Manager (Engineering)"
    r"|project\s+engineer"                   # "Project Engineer" (PM-adjacent in energy)
    r"|planning\s+manager"                   # "Planning Manager (Mechanical)"
    r"|package\s+manager"                    # "Package Manager — HVAC"
    # --- NEW (A1-b): energy-sector project-controls titles (EJL fix 2026-06-15) ---
    # In oil & gas / power / nuclear, planners own schedule delivery = PM role.
    r"|planning\s+engineer"                  # "Planning Engineer" (project controls)
    r"|project\s+planner"                    # "Project Planner"
    r"|(?:senior|lead)\s+planner"            # "Senior Planner", "Lead Planner"
    r"|project\s+controls?\s+(?:manager|engineer|lead|specialist)"  # "Project Controls Manager"
    r"|cost\s+control\s+(?:engineer|manager|specialist|lead)"       # "Cost Control Engineer" (energy PM-controls)
    r"|contracts?\s+engineer"                # "Contracts Engineer" (contract admin = PM function in energy)
    # --- NEW (v0.3): Assurance family ---
    # Precision backstop: passes_mechanical rejects IT/cyber assurance (no mech keywords).
    r"|assurance\s+(?:manager|engineer|lead|advisor)"  # "Assurance Manager/Engineer/Lead/Advisor"
    r"|quality\s+assurance"                            # "Quality Assurance (Engineer/Manager)"
    r"|safety\s+assurance"                             # "Safety Assurance Engineer"
    r"|nuclear\s+assurance"                            # "Nuclear Assurance Manager"
    r"|independent\s+assurance"                        # "Independent Assurance Lead"
    r"|\bsqa\b"                                        # SQA = Safety/Quality Assurance abbreviation
    # --- NEW (v0.3): Document Controller family ---
    # Precision backstop: passes_mechanical rejects software/IT doc controllers.
    r"|document\s+controller"                          # "Document Controller"
    r"|document\s+control\s+(?:manager|lead)"          # "Document Control Manager/Lead"
    r"|document\s+management"                          # "Document Management (Officer/Lead)"
    r"|records\s+(?:controller|manager)"               # "Records Controller/Manager"
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
# Merged list: original (44 items) + Tommy v0.2 MECH_KEYWORDS_ADDITIONS (57 items).
# Sorted alphabetically for maintainability.  Total: 101 terms.
# example: "HVAC design experience" → mech_score += 1
# example: "Rolling stock PM"       → mech_score += 3 (title)
MECH_KEYWORDS: list[str] = [
    "aerospace",
    "ahu",                   # Air Handling Unit
    "air conditioning",
    "air handling",
    "aircraft carrier",
    "airframe",
    "assembly line",
    "automotive",
    "avionics",
    "balance of plant",
    "battery storage",       # BESS projects have mech scope
    "bms",                   # Building Management System (mech-adjacent)
    "body in white",         # automotive manufacturing
    "boiler",
    "bop",                   # Balance of Plant
    "building services",
    "ccgt",                  # Combined Cycle Gas Turbine
    "chemical plant",
    "chilled water",
    "chiller",
    "cladding",              # often mech-eng scope on industrial builds
    "cnc",                   # CNC machining projects
    "combined cycle",
    "combustion",
    "compressor",
    "construction",
    "decommissioning",       # nuclear/oil decommissioning — heavy mech
    "depot",                 # train depot builds
    "district heating",
    "ductwork",
    "electrification",       # OLE = mech + elec
    "energy",
    "engine overhaul",
    "epc",                   # Engineering Procurement Construction
    "factory build",
    "feed",                  # Front End Engineering Design
    "fixture",               # jig and fixture (manufacturing)
    "fluid",
    "frigate",
    "gas turbine",
    "hazop",                 # Process safety — indicates heavy eng
    "heat exchanger",
    "heating",
    "hot water",
    "hull",
    "hvac",
    "jig",
    "lng",                   # Liquefied Natural Gas
    "locomotive",
    "m and e",               # some listings spell out "M and E"
    "m&e",
    "manufacturing",
    "marine",
    "mechanical",
    "mechanical fit-out",
    "mechanical fitout",
    "mechanical installation",
    "mep",                   # Mechanical Electrical Plumbing
    "mro",                   # Maintenance Repair Overhaul (aerospace)
    "naval",
    "nuclear",
    "offshore",
    "oil and gas",
    "p&id",                  # Piping & Instrumentation Diagram
    "paint shop",
    "permanent way",         # p-way = mech infrastructure
    "petrochemical",
    "pipework",
    "piping",
    "plant room",
    "plantroom",
    "power station",
    "powertrain",
    "press shop",
    "pressure vessel",
    "process plant",
    "production line",
    "propulsion",
    "pump",
    "rail",
    "railway",
    "refinery",
    "rolling stock",
    "rotating equipment",
    "shipbuilding",
    "signalling",            # often has mech-eng scope
    "solar farm",            # mech-heavy BoP
    "steam turbine",
    "steel erection",
    "structural steel",
    "submarine",
    "substation",            # switchgear/transformer = mech-adjacent
    "thermal",
    "tooling",
    "track renewal",
    "traction",
    "turbine",
    "ventilation",
    "warship",
    "wind farm",
    "wind turbine",
]

# Phrases that disqualify — indicate a non-mechanical-engineering domain.
# Matched with word-boundary regex (see _DISQUALIFY_RES below) so that
# "civil engineering" does NOT trigger the "civil engineer" entry.
# example: "software engineer"  → disqualify_score += 5 (in title)
# example: "IT project manager" → disqualify_score += 5 (in title)
DISQUALIFY_PHRASES: list[str] = [
    # --- original dev/IT disqualifiers ---
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
    "civil engineer",          # disqualifies civil-only, not construction PM
    "electrical engineer",     # disqualifies pure electrical, not M&E PM

    # --- NEW (A3): IT / Digital / Cyber (Tommy v0.2 DISQUALIFY_PHRASES_ADDITIONS) ---
    "it manager",
    "it director",
    "digital project manager",
    "digital programme manager",
    "digital transformation",
    "cyber security",
    "cybersecurity",
    "information security",
    "infosec",
    "scrum master",
    "agile coach",
    "product owner",
    "product manager",         # tech product manager ≠ project manager
    "release manager",
    "platform engineer",
    "site reliability",
    "sre",
    "infrastructure engineer",
    "systems engineer",        # usually IT systems, not mech systems
    "solutions architect",
    "technical architect",
    "enterprise architect",
    "data scientist",
    "data analyst",
    "machine learning",
    "ai engineer",
    "ux designer",
    "ui designer",
    "qa engineer",             # software QA
    "test engineer",           # software testing (≠ commissioning test eng)
    "automation engineer",     # usually RPA/software; NOT factory automation
    "business analyst",

    # --- NEW (A3): HR / Admin / Commercial ---
    "hr manager",
    "human resources",
    "recruitment manager",
    "recruitment consultant",
    "talent acquisition",
    "office manager",
    "operations manager",      # too generic without mech context
    "facilities manager",      # FM ≠ PM
    "account manager",
    "sales manager",
    "business development manager",
    "bdm",
    "marketing manager",
    "communications manager",
    "finance manager",
    "financial controller",
    "quantity surveyor",       # QS ≠ PM (construction but different discipline)
    "estimator",               # commercial role, not PM

    # --- NEW (A3): Healthcare / Life Sciences ---
    "clinical project manager",
    "pharmaceutical",
    "pharma",
    "biotech",
    "medical device",          # debatable — some are mech-eng; reject for now
    "clinical trial",

    # --- NEW (v0.2 Site Manager precision fix) ---
    # Site-supervisor signals that appear in description (not title) of labour
    # roles masquerading as project management.  Only consequential in the
    # construction-sector description check (passes_mechanical).
    "smsts",                   # Site Management Safety Training Scheme cert = site foreman
    "hands on role",           # "This is a hands on role overseeing day to day…" = labour supervisor
    "first aider",             # first-aid cert as a *required* qualification = site foreman signal
]

# Pre-compiled word-boundary patterns for DISQUALIFY_PHRASES.
# example: matches "civil engineer", not "civil engineering"
# example: matches "software engineer", not any non-word-bounded substring
_DISQUALIFY_RES: list[re.Pattern] = [
    re.compile(r"\b" + re.escape(ph) + r"\b", re.IGNORECASE)
    for ph in DISQUALIFY_PHRASES
]
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
    """True only for listings based in the UK.

    Decision logic — three mutually exclusive cases:
    1. country is a known non-UK code (EG, US, AE, …) → hard-reject; no flag,
       no Review Queue.  The extractor has positive evidence this is not UK.
    2. country == "GB" and location present → confirmed UK → pass.
    3. country == "GB" and location absent (defaulted):
       a. Non-UK geo signal found in title or description → hard-reject; no flag.
       b. No geo signal found anywhere → unknown country → soft-reject; append
          'country_unknown_assumed_non_uk' flag so the reporter routes to Review Queue.

    Conservative: unknown country is rejected, never silently assumed UK.
    The sanity flag is ONLY added in case 3b — never when a definitive non-UK
    code or geo signal is present.
    """
    # Case 1: positive evidence of a non-UK country → hard-reject, no flag
    if listing.country in _KNOWN_NON_UK_CODES:
        return False

    # Safety net: any unexpected non-GB code → hard-reject, no flag
    if listing.country != "GB":
        return False

    # Case 2: country is GB and location was provided → check location text is UK.
    # Defence-in-depth: scan location text even when country is already "GB".
    # Guards against stale country codes stored before a _NON_UK_MAP entry was added,
    # or any other code path that might set country="GB" for a non-UK location_raw.
    # example: location="Tokyo, Japan", country="GB" → hard-reject, no flag
    if listing.location.strip():
        for pattern, _ in _NON_UK_MAP:
            if pattern.search(listing.location):
                return False  # non-UK geo signal in location text → hard-reject
        return True

    # Case 3: country defaulted to GB because location was absent.
    # Corroborate via title then description_raw.
    for text in (listing.title or "", listing.description_raw or ""):
        for pattern, _ in _NON_UK_MAP:
            if pattern.search(text):
                return False  # Case 3a: non-UK geo signal → hard-reject, no flag

    # Case 3b: no geo signal at all → unknown country → soft-reject, Review Queue
    flag = "country_unknown_assumed_non_uk"
    if flag not in listing.sanity_flags:
        listing.sanity_flags.append(flag)
    return False


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

    Construction-sector refinement (v0.2 Site Manager fix):
    'construction' is assigned by keyword match (not a source-default vertical board)
    so it can capture generic site-supervision roles.  When the description contains
    site-supervisor disqualifier phrases (smsts, hands on role, first aider), the
    listing must also carry a *specific* mechanical keyword in the title — the word
    'construction' alone is excluded because it is circular evidence for this sector.
    """
    if listing.sector in _MECHANICAL_SECTORS:
        title_lower = (listing.title or "").lower()
        has_disqualifier = any(phrase in title_lower for phrase in DISQUALIFY_PHRASES)

        # Construction sector: also scan description for site-supervisor disqualifiers.
        # Rail / energy / aerospace etc. are source-default verticals and bypass this.
        if listing.sector == "construction":
            desc_lower = (listing.description_raw or "").lower()
            has_desc_disqualifier = any(pat.search(desc_lower) for pat in _DISQUALIFY_RES)
            # Only act on the description disqualifier when the TITLE itself did
            # NOT fire a disqualifier.  If the title already fired (e.g. "Quantity
            # Surveyor — Commercial Construction"), fall through to the existing
            # title-override check which uses the full MECH_KEYWORDS set.
            if has_desc_disqualifier and not has_disqualifier:
                # Description fired a site-supervisor signal (smsts, hands on role,
                # first aider) but the title looks clean.
                # Allow through only when title has a *specific* mech keyword beyond
                # 'construction' (circular for this sector).
                # example: "Mechanical Site Manager" + smsts in desc → title has
                #          "mechanical" → KEEP
                # example: "Site Manager" + smsts in desc → no specific mech → DROP
                # example: "Site Manager - Construction" + hands-on-role → title only
                #          has "construction" (excluded) → DROP
                _mech_not_construction = [kw for kw in MECH_KEYWORDS if kw != "construction"]
                return any(
                    re.search(r"\b" + re.escape(kw) + r"\b", title_lower)
                    for kw in _mech_not_construction
                )

        if not has_disqualifier:
            return True
        # Disqualifier matched in title: allow through only when a mechanical keyword
        # is *also* present in the title —
        # this handles multi-discipline titles like "Mechanical & Civil Engineering"
        # where mechanical content is the primary scope.
        # example: "Civil Engineering" fires disqualifier, no mech keyword → rejected
        # example: "Mechanical & Civil Engineering" fires disqualifier, "mechanical"
        #          present → passes (mech content overrides the disqualifier)
        return any(
            re.search(r"\b" + re.escape(kw) + r"\b", title_lower)
            for kw in MECH_KEYWORDS
        )

    # Generalist sector: keyword scoring
    title = (listing.title or "").lower()
    desc = (listing.description_raw or "").lower()

    mech_score = (
        3 * sum(1 for kw in MECH_KEYWORDS if re.search(r"\b" + re.escape(kw) + r"\b", title))
        + sum(1 for kw in MECH_KEYWORDS if re.search(r"\b" + re.escape(kw) + r"\b", desc))
    )
    disqualify_score = (
        5 * sum(1 for pat in _DISQUALIFY_RES if pat.search(title))
        + 2 * sum(1 for pat in _DISQUALIFY_RES if pat.search(desc))
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
