"""Sector assignment for NormalizedListing.

Dual strategy:
  1. Source default — vertical boards carry an inherent sector.
  2. Keyword map — scan title + description for domain signals; pick the
     sector with the most hits (title hits count double).  Fall through to
     'generalist' when no signal is found.

All keyword lists are case-insensitive word-boundary matches.

2026-06-12
"""
from __future__ import annotations

import re

from mechpm.adapters.base import RawListing

# ---------------------------------------------------------------------------
# Source defaults
# Three vertical boards always produce listings for a single sector.
# ---------------------------------------------------------------------------
SOURCE_DEFAULTS: dict[str, str] = {
    "railwaypeople": "rail",
    "energy_jobline": "energy",
    "aviation_job_search": "aerospace",
}

# ---------------------------------------------------------------------------
# Keyword map — sector → keywords
# Matching is case-insensitive, word-boundary anchored.
# ---------------------------------------------------------------------------
KEYWORD_MAP: dict[str, list[str]] = {
    "rail": [
        "rolling stock",
        "permanent way",
        "signalling",
        "network rail",
        "hs2",
        "crossrail",
        "tfl",
        "alstom",
        "siemens mobility",
        "hitachi rail",
        "rail infrastructure",
        "railway",
        "traction",
        "overhead line",
        "train",
    ],
    "aerospace": [
        "airframe",
        "avionics",
        "aircraft",
        "airbus",
        "rolls-royce",
        "bae systems",
        "boeing",
        "aerospace",
        "aerostructure",
        "helicopter",
        "propulsion system",
    ],
    "defence": [
        "mod",
        "ministry of defence",
        "submarine",
        "naval architecture",
        "weapons system",
        "defence",
        "dstl",
        "qinetiq",
        "thales",
        "mbda",
        "armament",
    ],
    "energy": [
        "oil and gas",
        "offshore",
        "renewable",
        "wind turbine",
        "solar farm",
        "power station",
        "edf",
        "national grid",
        "energy",
        "lng",
        "fpso",
        "upstream",
        "downstream",
        "onshore",
        "shell",
        "bp",
    ],
    "nuclear": [
        "nuclear",
        "sellafield",
        "edf nuclear",
        "amec foster wheeler",
        "nda",
        "decommissioning",
        "reactor",
        "radioactive",
        "nnb",
        "hinkley",
    ],
    "construction": [
        "m&e",
        "building services",
        "hvac",
        "main contractor",
        "mechanical and electrical",
        "construction",
        "fit-out",
        "refurbishment",
        "facilities management",
        "structural steel",
        "groundworks",
        "civils",
    ],
    "maritime": [
        "shipyard",
        "marine engineering",
        "vessel",
        "dnv",
        "lloyds register",
        "navy",
        "frigate",
        "destroyer",
        "submarine hull",
        "imo",
        "offshore vessel",
    ],
    "automotive": [
        "automotive",
        "vehicle",
        "powertrain",
        "oem",
        "tier 1 supplier",
        "jlr",
        "jaguar land rover",
        "bmw",
        "ford",
        "stellantis",
        "vauxhall",
        "battery electric vehicle",
    ],
    "process": [
        "process plant",
        "chemical plant",
        "refinery",
        "petrochemical",
        "distillation",
        "heat exchanger",
        "reactor vessel",
        "piping",
        "p&id",
        "hazop",
        "pharmaceutical plant",
    ],
}

# Pre-compile keyword patterns once at import time for fast repeated use.
_KEYWORD_PATTERNS: dict[str, list[re.Pattern]] = {
    sector: [
        re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in keywords
    ]
    for sector, keywords in KEYWORD_MAP.items()
}


def assign_sector(raw: RawListing) -> str:
    """Assign a sector label to *raw*.

    Strategy:
      1. SOURCE_DEFAULTS[raw.source] — vertical boards have a fixed sector.
      2. Keyword scoring on raw.title (×2) + raw.description_raw (×1).
      3. Pick sector with highest score; ties resolved by KEYWORD_MAP key order.
      4. Default 'generalist' when no signal is found.
    """
    # 1. Source default
    source_default = SOURCE_DEFAULTS.get(raw.source)
    if source_default:
        return source_default

    # 2. Keyword scoring
    title = raw.title or ""
    desc = raw.description_raw or ""

    scores: dict[str, int] = {}
    for sector, patterns in _KEYWORD_PATTERNS.items():
        title_hits = sum(1 for p in patterns if p.search(title))
        desc_hits = sum(1 for p in patterns if p.search(desc))
        total = title_hits * 2 + desc_hits
        if total > 0:
            scores[sector] = total

    if not scores:
        return "generalist"

    # Pick sector with highest score; ties favour first in KEYWORD_MAP order.
    return max(scores, key=lambda s: scores[s])
