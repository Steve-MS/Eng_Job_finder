"""Tier-1 extraction: lift fields directly from RawListing without parsing.

These are the 'free' fields — values the adapter has already structured.
Returns a partial dict; remaining fields are filled by the regex and LLM tiers.

2026-06-12
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from mechpm.adapters.base import RawListing


def extract_structured(raw: RawListing) -> dict:
    """Return a partial field-dict populated entirely from RawListing attributes.

    Fields included here require zero parsing — they map 1-to-1 from the
    adapter's output to the normalised schema.
    """
    desc_raw = raw.description_raw
    return {
        "source": raw.source,
        "source_listing_id": raw.source_listing_id,
        "source_url": raw.url,
        "source_urls": [raw.url],
        "title": raw.title,
        "employer": raw.employer,
        "agency": raw.agency,
        "location": raw.location_raw or "",       # Polly display field
        "location_raw_value": raw.location_raw,   # kept for regex tier
        "posted_at": raw.posted_at,
        "description_raw": desc_raw,
        "description_clean": _clean_html(desc_raw),
        "last_seen_at": raw.fetched_at,
        "discovered_at": datetime.now(timezone.utc),
    }


def _clean_html(raw: str | None) -> str | None:
    """Strip HTML tags, unescape entities, and collapse whitespace."""
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
