"""
tests/test_ejl_regression.py — Regression tests for the Energy Jobline T4 gate fix.

Root cause (2026-06-15): EJL ?location=United+Kingdom URL param does NOT filter
globally; the v0.2 multi-query expansion ("project manager", "engineering manager
contract") fetched 101 global listings, all of which failed passes_uk → 0 stored.

Fix:
  1. config.toml: keywords now embed "United Kingdom" so EJL text-search returns
     UK-located listings (EJL embeds location in job titles).
  2. energy_jobline.py: _is_clearly_non_uk() post-fetch guard drops confirmed
     non-UK listings before they enter the pipeline.
  3. regex_fields.py: _NON_UK_MAP extended to cover Spain, Italy, Brazil, China,
     Malaysia, Australia, Mexico, etc. (19 false-positive UK classifications seen).

Regression tests:
  A. _is_clearly_non_uk() correctly identifies non-UK / uncertain listings.
  B. Expanded _NON_UK_MAP correctly rejects non-UK location_raw values.
  C. A realistic EJL UK PM listing (Aberdeen, "Project Manager" title) passes
     all four filters end-to-end.
  D. A non-UK EJL listing (Sacramento USA) is rejected by passes_uk.
"""

from __future__ import annotations

import pytest

try:
    from mechpm.adapters.base import RawListing
    from mechpm.adapters.energy_jobline import _is_clearly_non_uk
    from mechpm.extractor import extract
    from mechpm.extractor.filters import passes_all, passes_uk
    from mechpm.extractor.regex_fields import detect_country

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_SKIP = pytest.mark.skipif(not _AVAILABLE, reason="mechpm modules not available")


# ---------------------------------------------------------------------------
# A.  _is_clearly_non_uk() unit tests
# ---------------------------------------------------------------------------

@_SKIP
@pytest.mark.parametrize("location_raw,title,expected", [
    # Confirmed non-UK
    ("Sacramento, CA, USA",           "Project Manager",                True),
    ("Binghamton, NY, USA",           "Lead Engineer",                  True),
    ("Sassnitz, Germany",             "Deputy LSAP",                    True),
    ("Madrid, Spain",                 "Técnico/a Ciberseguridad",       True),
    ("Milan, Metropolitan City of Milan, Italy", "Machining Shop Operator", True),
    ("Taubaté - State of São Paulo, Brazil", "Agente de Faturamento",   True),
    ("Haikou, Hainan, China",         "Reliability Engineer",           True),
    ("Kuala Lumpur, Malaysia",        "Global Tax Analyst",             True),
    ("Bangkok, Thailand",             "EHS Specialist",                 True),
    ("Jandakot WA 6164, Australia",   "Field Specialist",               True),
    ("Santiago de Querétaro, Mexico", "Mechanical Design Engineer",     True),
    # None location but non-UK signal in title
    (None, "Lead Engineer - Civil in United States Of America, New York", True),
    (None, "Estimating Manager in Germany, Berlin",                      True),
    # Confirmed / likely UK
    ("Aberdeen, Scotland",            "Project Manager – Offshore Wind", False),
    ("Blantyre, UK",                  "Senior Planner",                 False),
    ("Birmingham, UK",                "Commercial Insurance Manager",   False),
    # Uncertain (no signal) — conservative: keep for passes_uk to decide
    (None, "Power System Engineer in 5 Locations",                       False),
    (None, "Senior Engineers - Protection and Control in 4 Locations",   False),
])
def test_is_clearly_non_uk(location_raw, title, expected):
    """_is_clearly_non_uk() correctly identifies non-UK vs uncertain."""
    result = _is_clearly_non_uk(location_raw, title)
    assert result is expected, (
        f"_is_clearly_non_uk({location_raw!r}, {title!r}) = {result}, "
        f"expected {expected}"
    )


# ---------------------------------------------------------------------------
# B.  detect_country() extended coverage for previously-missing countries
# ---------------------------------------------------------------------------

@_SKIP
@pytest.mark.parametrize("location_raw,expected_country", [
    # Countries added in the _NON_UK_MAP expansion
    ("Madrid, Spain",                   "ES"),
    ("Milan, Metropolitan City of Milan, Italy", "IT"),
    ("São Paulo, Brazil",               "BR"),
    ("Haikou, Hainan, China",           "CN"),
    ("Kuala Lumpur, Malaysia",          "MY"),
    ("Jandakot WA 6164, Australia",     "AU"),
    ("Santiago de Querétaro, Mexico",   "MX"),
    ("Bangkok, Thailand",               "TH"),
    ("Maputo, Mozambique",              "MZ"),
    ("Oslo, Norway",                    "NO"),
    ("Prague, Czechia",                 "CZ"),
    ("Pilsen, Czechia",                 "CZ"),
    # Already covered — regression guard
    ("Dubai, UAE",                      "AE"),
    ("Berlin, Germany",                 "DE"),
    ("Paris, France",                   "FR"),
    ("Sacramento, CA, USA",             "US"),
    # UK — must remain GB
    ("Aberdeen, Scotland",              "GB"),
    ("Blantyre, UK",                    "GB"),
    ("Leeds",                           "GB"),
])
def test_detect_country_extended(location_raw, expected_country):
    """detect_country() now returns the correct country for previously-missed locales."""
    result = detect_country(location_raw)
    assert result == expected_country, (
        f"detect_country({location_raw!r}) = {result!r}, expected {expected_country!r}"
    )


# ---------------------------------------------------------------------------
# C.  End-to-end: realistic EJL UK PM listing passes all four filters
# ---------------------------------------------------------------------------

@_SKIP
def test_ejl_uk_pm_listing_passes_all_filters():
    """A realistic EJL UK mechanical-PM contract listing passes all four filters.

    This is the exact listing shape that was ZERO-stored in v0.2 because the
    _NON_UK_MAP didn't cover enough countries and EJL queries were too broad.
    With the fix, UK listings (Aberdeen location + 'Project Manager' title)
    should survive the full filter chain.
    """
    raw = RawListing(
        source="energy_jobline",
        url="https://www.energyjobline.com/job/project-manager-offshore-28999001",
        source_listing_id="28999001",
        title="Project Manager – Offshore Mechanical in United Kingdom, Aberdeen",
        employer="Brunel Energy",
        location_raw="Aberdeen, Scotland",
        description_raw=None,   # detail fetch skipped for MVP
        contract_type_raw="contract",
        salary_raw=None,
        metadata={"detail_fetched": False},
    )
    normalized = extract(raw)

    assert normalized.country == "GB", f"Expected GB, got {normalized.country!r}"
    assert normalized.sector == "energy", f"Expected energy sector, got {normalized.sector!r}"
    assert normalized.contract_type == "contract"

    passed, failures = passes_all(normalized)
    assert passed, (
        f"EJL UK PM listing should pass all filters but failed: {failures}\n"
        f"  title={normalized.title!r}\n"
        f"  country={normalized.country!r}\n"
        f"  location={normalized.location!r}\n"
        f"  sector={normalized.sector!r}\n"
        f"  contract_type={normalized.contract_type!r}"
    )


@_SKIP
def test_ejl_offshore_wind_pm_passes():
    """'Project Manager – Offshore Wind' with UK location passes all filters."""
    raw = RawListing(
        source="energy_jobline",
        url="https://www.energyjobline.com/job/pm-offshore-wind-29100001",
        source_listing_id="29100001",
        title="Project Manager – Offshore Wind Balance of Plant in United Kingdom, Lowestoft",
        employer=None,
        agency="Spencer Ogden",
        location_raw="Lowestoft, UK",
        description_raw=None,
        contract_type_raw="contract",
        salary_raw=None,
        metadata={"detail_fetched": False},
    )
    normalized = extract(raw)
    passed, failures = passes_all(normalized)
    assert passed, f"Offshore Wind PM failed: {failures}"


# ---------------------------------------------------------------------------
# C2. Energy-sector project-controls titles (new PM_TITLE_RE entries, 2026-06-15)
#     In oil & gas / power generation, planners own schedule delivery = PM role.
# ---------------------------------------------------------------------------

@_SKIP
@pytest.mark.parametrize("title,location_raw", [
    # Titles seen on live EJL (Blantyre, UK) — these triggered the EJL T4 gap
    ("Senior Planner in United Kingdom, Blantyre",    "Blantyre, UK"),
    ("Planning Engineer in United Kingdom, Blantyre", "Blantyre, UK"),
    # Live EJL UK listing (project-controls) seen 2026-06-15
    ("Contracts & Cost Control Engineer",             "United Kingdom"),
    # Canonical energy PM-controls titles
    ("Project Planner – Offshore Oil & Gas",          "Aberdeen, Scotland"),
    ("Lead Planner – CCGT Power Station",             "Leeds, UK"),
    ("Project Controls Manager – Offshore Wind",      "Lowestoft, UK"),
    ("Project Controls Engineer – Nuclear Decommissioning", "Sellafield, UK"),
    ("Project Controls Lead – LNG Plant",             "Milford Haven, UK"),
    ("Cost Control Manager – Power Station",          "Bristol, UK"),
    ("Contracts Engineer – Offshore Wind Farm",       "Grimsby, UK"),
])
def test_energy_project_controls_pm_titles_pass(title, location_raw):
    """Energy-sector project-controls titles now pass passes_pm_role (A1-b fix)."""
    raw = RawListing(
        source="energy_jobline",
        url=f"https://www.energyjobline.com/job/test-{abs(hash(title)) % 100000}",
        source_listing_id=str(abs(hash(title)) % 100000),
        title=title,
        employer=None,
        location_raw=location_raw,
        description_raw=None,
        contract_type_raw="contract",
        salary_raw=None,
        metadata={"detail_fetched": False},
    )
    normalized = extract(raw)
    passed, failures = passes_all(normalized)
    assert passed, (
        f"Energy project-controls title {title!r} should pass all filters, "
        f"failed: {failures}"
    )


# ---------------------------------------------------------------------------
# D.  Non-UK EJL listings are rejected by passes_uk
# ---------------------------------------------------------------------------

@_SKIP
@pytest.mark.parametrize("location_raw,title,expected_country", [
    ("Sacramento, CA, USA",             "Critical Infrastructure Project Manager", "US"),
    ("Milan, Metropolitan City of Milan, Italy", "Estimating and Proposal Manager", "IT"),
    ("Madrid, Spain",                   "Técnico/a Ciberseguridad",                "ES"),
    ("Haikou, Hainan, China",           "Mechanical Design Engineer",              "CN"),
    ("Jandakot WA 6164, Australia",     "Field Specialist",                        "AU"),
])
def test_non_uk_ejl_listing_rejected_by_passes_uk(location_raw, title, expected_country):
    """Non-UK EJL listings are correctly rejected by passes_uk after _NON_UK_MAP expansion."""
    raw = RawListing(
        source="energy_jobline",
        url=f"https://www.energyjobline.com/job/test-{expected_country.lower()}-99999",
        source_listing_id=f"non_uk_{expected_country.lower()}",
        title=title,
        employer=None,
        location_raw=location_raw,
        description_raw=None,
        contract_type_raw="contract",
        salary_raw=None,
        metadata={"detail_fetched": False},
    )
    normalized = extract(raw)
    assert normalized.country == expected_country, (
        f"Expected country={expected_country!r} for {location_raw!r}, "
        f"got {normalized.country!r}"
    )
    assert not passes_uk(normalized), (
        f"Non-UK listing ({location_raw!r}) should fail passes_uk "
        f"but was accepted (country={normalized.country!r})"
    )
