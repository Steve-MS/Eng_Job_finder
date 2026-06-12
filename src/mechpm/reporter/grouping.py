"""Region grouping and per-listing flag helpers.

The 8 canonical region buckets are:
    London / South-East / Midlands / North / Scotland / Wales / Remote / Other

NOTE on South-West: No dedicated South-West bucket exists in the 8-region
scheme.  Bristol, Swindon, Bath, Exeter, and other South-West cities are
mapped to "South-East" — the nearest broad southern region — and this is
documented in Polly's history.md.

Dedup safety: ``group_by_region`` maps every listing to *exactly one* bucket
via ``resolve_region``, so a listing can never bleed into multiple region
sections.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from mechpm.models import NormalizedListing

# ---------------------------------------------------------------------------
# Region ordering (controls section order in the report)
# ---------------------------------------------------------------------------
REGION_ORDER: list[str] = [
    "London",
    "South-East",
    "Midlands",
    "North",
    "Scotland",
    "Wales",
    "Remote",
    "Other",
]

# ---------------------------------------------------------------------------
# Location keyword → region map.
#
# Checked in declaration order; first match wins.  Keywords are matched as
# substrings against the lower-cased ``location_normalized`` field.
# ---------------------------------------------------------------------------
_REGION_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    # Remote beats any geo keyword.
    (
        ("remote", "work from home", "wfh", "home based", "home-based",
         "nationwide", "uk-wide", "uk wide"),
        "Remote",
    ),
    # London — before South-East so "london" wins over generic south-east terms.
    (
        ("london", "canary wharf", "city of london"),
        "London",
    ),
    # Scotland
    (
        ("scotland", "edinburgh", "glasgow", "aberdeen", "dundee", "inverness",
         "stirling", "paisley", "kilmarnock", "falkirk", "perth", "kirkcaldy",
         "livingston", "east kilbride", "clydebank", "motherwell"),
        "Scotland",
    ),
    # Wales
    (
        ("wales", "cardiff", "newport", "swansea", "wrexham", "merthyr",
         "bridgend", "barry", "port talbot", "caerphilly", "neath",
         "rhondda", "llanelli"),
        "Wales",
    ),
    # North (England) — checked before Midlands to avoid Cheshire/Lancashire bleed.
    (
        ("manchester", "liverpool", "leeds", "newcastle", "sheffield",
         "bradford", "hull", "sunderland", "middlesbrough", "durham",
         "york", "wakefield", "huddersfield", "rotherham", "doncaster",
         "barnsley", "wigan", "bolton", "stockport", "oldham", "rochdale",
         "salford", "preston", "blackpool", "lancaster", "carlisle",
         "chester", "crewe", "warrington", "wirral", "birkenhead",
         "halifax", "harrogate", "scarborough", "northumberland", "cumbria",
         "yorkshire", "lancashire", "cheshire", "merseyside",
         "tyne and wear", "county durham", "teesside",
         "north east", "north west", "greater manchester"),
        "North",
    ),
    # Midlands
    (
        ("birmingham", "coventry", "derby", "nottingham", "leicester",
         "stoke-on-trent", "stoke on trent", "wolverhampton", "dudley",
         "walsall", "solihull", "stafford", "staffordshire", "shrewsbury",
         "telford", "northampton", "lincoln", "lincolnshire", "loughborough",
         "burton", "tamworth", "lichfield", "redditch", "worcester",
         "hereford", "warwick", "leamington", "east midlands", "west midlands",
         "warwickshire", "shropshire", "northamptonshire", "leicestershire",
         "nottinghamshire", "derbyshire"),
        "Midlands",
    ),
    # South-East (absorbs South-West — no South-West bucket in the 8-region scheme).
    (
        ("surrey", "kent", "hampshire", "berkshire", "oxfordshire", "sussex",
         "reading", "portsmouth", "southampton", "brighton", "guildford",
         "maidstone", "crawley", "basingstoke", "winchester", "isle of wight",
         "hertfordshire", "essex", "bedfordshire", "buckinghamshire",
         "norfolk", "suffolk", "cambridge", "luton", "stevenage", "watford",
         "st albans", "slough", "windsor", "wokingham", "bracknell",
         "maidenhead", "aldershot", "farnborough", "eastleigh", "woking",
         "epsom", "reigate", "horsham", "worthing", "hastings", "folkestone",
         "dover", "margate", "tunbridge wells", "chelmsford", "colchester",
         "ipswich", "norwich", "peterborough", "oxford",
         # South-West absorbed into South-East:
         "bristol", "bath", "swindon", "exeter", "plymouth", "gloucester",
         "cheltenham", "stroud", "taunton", "yeovil", "dorset", "somerset",
         "devon", "cornwall", "wiltshire", "gloucestershire",
         "south west", "south-west", "avon",
         # Generic label:
         "south east", "south-east"),
        "South-East",
    ),
]


def resolve_region(location_normalized: str) -> str:
    """Map a normalised location string to one of the 8 canonical region buckets."""
    loc = location_normalized.lower()
    for keywords, region in _REGION_KEYWORDS:
        for kw in keywords:
            if kw in loc:
                return region
    return "Other"


def group_by_region(
    listings: list[NormalizedListing],
) -> dict[str, list[NormalizedListing]]:
    """Partition *listings* into region buckets preserving REGION_ORDER.

    Each listing maps to exactly one bucket — no cross-section bleed.
    Regions with no listings are omitted from the returned dict.
    """
    buckets: dict[str, list[NormalizedListing]] = defaultdict(list)
    for listing in listings:
        region = resolve_region(listing.location_normalized)
        buckets[region].append(listing)

    # Return in canonical order, skipping empty regions.
    return {r: buckets[r] for r in REGION_ORDER if buckets[r]}


# ---------------------------------------------------------------------------
# Per-listing flag predicates
# ---------------------------------------------------------------------------

def is_new(listing: NormalizedListing, previous_run_listing_ids: set[str]) -> bool:
    """Return True if the listing did not exist in the previous run.

    Delegates to the pre-computed ``is_new_listing`` flag when the caller has
    not loaded a previous-run ID set (pass ``set()`` in that case and rely on
    the flag Ada's orchestrator sets).
    """
    if previous_run_listing_ids:
        return listing.listing_id not in previous_run_listing_ids
    return listing.is_new_listing


def is_urgent(listing: NormalizedListing, today: date) -> bool:
    """Return True if ``start_date`` is within 14 calendar days of *today*."""
    if listing.start_date is None:
        return False
    delta = (listing.start_date - today).days
    return 0 <= delta <= 14


def is_premium(listing: NormalizedListing) -> bool:
    """Return True when rate ≥ £700/day AND IR35 is outside."""
    rate = listing.day_rate_max or listing.day_rate_min
    if rate is None:
        return False
    return rate >= 700 and listing.ir35_status == "outside"


def get_sanity_reasons(listing: NormalizedListing, today: date | None = None) -> list[str]:
    """Return all sanity-flag reasons for a listing (empty list = clean)."""
    from mechpm.reporter.domain import scan_red_flags

    reasons: list[str] = list(listing.sanity_flags)  # include Ada's extraction flags
    _today = today or date.today()

    rate_hi = listing.day_rate_max
    rate_lo = listing.day_rate_min
    effective = rate_hi or rate_lo

    if effective is not None:
        if effective <= 250:
            reasons.append(f"Rate £{effective:,.0f}/day is suspiciously low (≤ £250)")
        if effective >= 1500:
            reasons.append(f"Rate £{effective:,.0f}/day is unusually high (≥ £1500)")
        if effective >= 700 and listing.ir35_status in (None, "undetermined"):
            reasons.append(
                f"Rate £{effective:,.0f}/day ≥ £700 but IR35 status not stated — "
                "review before quoting to Steve"
            )

    if listing.start_date is not None:
        days_delta = (listing.start_date - _today).days
        if days_delta < 0:
            reasons.append(
                f"Start date {listing.start_date} is in the past ({abs(days_delta)} days ago)"
            )

    if listing.duration_weeks is not None and listing.duration_weeks > 96:
        reasons.append(
            f"Duration {listing.duration_weeks} weeks (>{listing.duration_weeks // 4} months) "
            "— likely a permanent role mis-labelled as contract"
        )

    if listing.day_rate_min is None and listing.day_rate_max is None:
        reasons.append("Day rate missing — cannot benchmark or flag premium status")

    if not listing.location_normalized.strip():
        reasons.append("Location vague or missing — unable to assign region")

    # Scan description for red-flag language.
    if listing.description_clean:
        text_flags = scan_red_flags(listing.description_clean)
        reasons.extend(text_flags)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def is_sanity_flagged(listing: NormalizedListing, today: date | None = None) -> bool:
    """Return True if the listing triggers any sanity rule."""
    return bool(get_sanity_reasons(listing, today))
