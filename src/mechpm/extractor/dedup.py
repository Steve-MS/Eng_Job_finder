"""Deduplication of NormalizedListing records.

Strategy:
  Block   → (location_key × duration_bucket) limits O(n²) comparisons.
  Match   → Jaro-Winkler title similarity ≥ 0.85
             + rate-band overlap ≥ 50 % (skipped when either lacks rate)
             + posted_at within 14-day window (skipped when either lacks date)
             + employer loose word-match (skipped when either is unknown).
  Merge   → canonical = highest-priority source; widest rate range; stated
             IR35 over None; latest last_seen_at; union of source_urls.

Requires: rapidfuzz >= 3.0 (optional dep — identity fallback if missing).

2026-06-12
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple

from mechpm.models import NormalizedListing

logger = logging.getLogger("mechpm.extractor.dedup")

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
_JARO_THRESHOLD = 0.85    # Jaro-Winkler title similarity
_RATE_OVERLAP_MIN = 0.50  # minimum rate-band overlap fraction
_WINDOW_DAYS = 14         # max days between postings to consider duplicate

# Source priority (lower index = higher priority / more authoritative).
_SOURCE_PRIORITY: list[str] = [
    "reed",
    "totaljobs",
    "cwjobs",
    "railwaypeople",
    "energy_jobline",
    "aviation_job_search",
    "the_engineer",
]


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

class DedupResult(NamedTuple):
    """Result of a deduplication pass."""
    listings: list[NormalizedListing]
    groups: dict[str, list[str]]  # canonical listing_id → [merged listing_ids]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dedupe(listings: list[NormalizedListing]) -> list[NormalizedListing]:
    """Deduplicate *listings*; return canonical records only.

    Implements the full blocking + Jaro-Winkler + rate-overlap pipeline.
    Falls back to identity (no dedup) if rapidfuzz is not installed.
    """
    return dedupe_with_groups(listings).listings


def dedupe_with_groups(listings: list[NormalizedListing]) -> DedupResult:
    """Deduplicate and return both canonical listings and the merge-group map.

    groups maps each canonical listing_id to a list of the listing_ids that
    were merged into it (non-empty only when duplicates were found).
    """
    try:
        from rapidfuzz.distance import JaroWinkler  # type: ignore[import]
    except ImportError:
        logger.warning(
            "rapidfuzz not installed — dedup is identity (no deduplication). "
            "Add 'rapidfuzz>=3.0' to pyproject.toml."
        )
        return DedupResult(list(listings), {})

    if not listings:
        return DedupResult([], {})

    # Build blocks: location_key × duration_bucket
    blocks: dict[str, list[NormalizedListing]] = {}
    for listing in listings:
        key = _block_key(listing)
        blocks.setdefault(key, []).append(listing)

    # Process each block
    merged: dict[str, NormalizedListing] = {}   # listing_id → canonical record
    groups: dict[str, list[str]] = {}            # canonical_id → [merged ids]

    for block_listings in blocks.values():
        _process_block(block_listings, merged, groups, JaroWinkler)

    canonical_list = sorted(merged.values(), key=lambda x: x.listing_id)
    return DedupResult(canonical_list, groups)


# ---------------------------------------------------------------------------
# Block processing
# ---------------------------------------------------------------------------

def _process_block(
    block: list[NormalizedListing],
    merged: dict[str, NormalizedListing],
    groups: dict[str, list[str]],
    JaroWinkler: object,
) -> None:
    """Process one dedup block, updating merged and groups in-place."""
    block_ids: set[str] = {l.listing_id for l in block}

    for listing in block:
        lid = listing.listing_id

        # Has this listing already been absorbed as a duplicate of something in
        # a different block that also landed a member in this block?
        if lid in {mid for mids in groups.values() for mid in mids}:
            continue

        matched_canonical_id: str | None = None

        # Compare against existing canonical records that share this block.
        for cid, existing in merged.items():
            if cid not in block_ids:
                continue
            if _is_duplicate(listing, existing, JaroWinkler):
                matched_canonical_id = cid
                break

        if matched_canonical_id:
            # Merge into existing canonical
            existing = merged[matched_canonical_id]
            merged[matched_canonical_id] = _merge(existing, listing)
            groups.setdefault(matched_canonical_id, []).append(lid)
        else:
            # New canonical record
            merged[lid] = listing
            groups.setdefault(lid, [])


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def _is_duplicate(
    a: NormalizedListing,
    b: NormalizedListing,
    JaroWinkler: object,
) -> bool:
    """Return True when a and b are likely the same role from different sources."""
    # Title Jaro-Winkler similarity (mandatory gate)
    sim: float = JaroWinkler.similarity(  # type: ignore[attr-defined]
        a.title.lower(), b.title.lower()
    )
    if sim < _JARO_THRESHOLD:
        return False

    # Rate band overlap ≥ 50 % (skip when either has no rate — no penalty)
    if a.day_rate_min is not None and b.day_rate_min is not None:
        if _rate_overlap(a, b) < _RATE_OVERLAP_MIN:
            return False

    # Posting date within 14-day window (skip when either lacks date)
    if a.posted_at is not None and b.posted_at is not None:
        delta_seconds = abs((a.posted_at - b.posted_at).total_seconds())
        if delta_seconds > _WINDOW_DAYS * 86_400:
            return False

    # Employer loose match (skip when either is unknown)
    if a.employer and b.employer:
        if not _employers_loosely_match(a.employer, b.employer):
            return False

    return True


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def _merge(
    canonical: NormalizedListing,
    duplicate: NormalizedListing,
) -> NormalizedListing:
    """Merge duplicate into canonical, returning an updated canonical record.

    Rules:
      - canonical = whichever has the higher source priority (lower index).
      - source_urls = union in order (canonical first).
      - rate = widest combined range.
      - ir35_status = prefer stated over None.
      - last_seen_at = max of the two.
    """
    # If duplicate is higher priority, swap roles.
    if _source_rank(duplicate.source) < _source_rank(canonical.source):
        return _merge(duplicate, canonical)

    all_urls = list(dict.fromkeys(canonical.source_urls + duplicate.source_urls))

    # Prefer a stated IR35 value over None.
    ir35 = canonical.ir35_status
    if ir35 is None and duplicate.ir35_status is not None:
        ir35 = duplicate.ir35_status

    # Widest rate range.
    rate_min = _pick_min(canonical.day_rate_min, duplicate.day_rate_min)
    rate_max = _pick_max(canonical.day_rate_max, duplicate.day_rate_max)

    # Latest last_seen_at.
    last_seen = max(canonical.last_seen_at, duplicate.last_seen_at)

    return canonical.model_copy(update={
        "source_urls": all_urls,
        "ir35_status": ir35,
        "day_rate_min": rate_min,
        "day_rate_max": rate_max,
        "last_seen_at": last_seen,
    })


# ---------------------------------------------------------------------------
# Block-key helpers
# ---------------------------------------------------------------------------

def _block_key(listing: NormalizedListing) -> str:
    loc = _location_key(listing.location_normalized or listing.location)
    bucket = _duration_bucket(listing.duration_weeks)
    return f"{loc}|{bucket}"


def _location_key(location: str) -> str:
    if not location:
        return "unknown"
    loc = location.split(",")[0].strip().lower()
    # Strip trailing postcode-like suffix
    loc = re.sub(r"\b[a-z]{1,2}\d{1,2}[a-z]?\b", "", loc, flags=re.IGNORECASE).strip()
    return loc or "unknown"


def _duration_bucket(weeks: int | None) -> str:
    if weeks is None:
        return "unknown"
    if weeks <= 4:
        return "0-4w"
    if weeks <= 13:
        return "5-13w"
    if weeks <= 26:
        return "14-26w"
    if weeks <= 52:
        return "27-52w"
    return "52w+"


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

def _rate_overlap(a: NormalizedListing, b: NormalizedListing) -> float:
    """Fraction of overlap between two rate ranges (0.0–1.0).

    Returns 1.0 when both lack max (no penalty for incomplete rate info).
    """
    a_min = a.day_rate_min or 0.0
    a_max = a.day_rate_max or a_min
    b_min = b.day_rate_min or 0.0
    b_max = b.day_rate_max or b_min
    overlap = max(0.0, min(a_max, b_max) - max(a_min, b_min))
    span = max(a_max, b_max) - min(a_min, b_min)
    if span == 0:
        return 1.0
    return overlap / span


def _employers_loosely_match(a: str, b: str) -> bool:
    """True if employer names share at least one non-trivial word."""
    _STOP = frozenset({"ltd", "limited", "plc", "llp", "inc", "group",
                       "services", "the", "and", "&", "uk", "of"})
    words_a = {w.lower() for w in a.split() if w.lower() not in _STOP and len(w) > 2}
    words_b = {w.lower() for w in b.split() if w.lower() not in _STOP and len(w) > 2}
    return bool(words_a & words_b)


def _source_rank(source: str) -> int:
    try:
        return _SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(_SOURCE_PRIORITY)  # unknown sources get lowest priority


def _pick_min(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _pick_max(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
