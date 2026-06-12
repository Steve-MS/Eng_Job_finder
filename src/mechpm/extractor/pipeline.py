"""Extraction pipeline — orchestrates Tier-1 → Tier-2 → Tier-3 per field.

Tier 1 (structured.py)  : fields lifted directly from RawListing
Tier 2 (regex_fields.py): rate, IR35, duration, dates, contract type, remote
                          policy, location normalisation, country detection
Tier 3 (llm_fallback.py): fuzzy extraction when Tier-2 yields no result
                          (gated by MECHPM_LLM_FALLBACK env var)

Usage:
    from mechpm.extractor.pipeline import extract
    normalized = extract(raw)

2026-06-12
"""
from __future__ import annotations

import logging
import os

from mechpm.adapters.base import RawListing
from mechpm.models import NormalizedListing
from mechpm.extractor import structured as tier1
from mechpm.extractor import regex_fields as tier2
from mechpm.extractor import sector as sector_mod
from mechpm.extractor import llm_fallback as tier3

logger = logging.getLogger("mechpm.extractor.pipeline")

# Set MECHPM_LLM_FALLBACK=1 to enable LLM calls for gap-filling.
# Off by default to prevent unexpected OpenAI API calls during dev/test.
ENABLE_LLM_FALLBACK: bool = os.getenv("MECHPM_LLM_FALLBACK", "0") == "1"


def extract(raw: RawListing, llm_client: object | None = None) -> NormalizedListing:
    """Orchestrate the three-tier extraction pipeline for a single RawListing.

    Args:
        raw:        RawListing from any adapter.
        llm_client: Injectable OpenAI-compatible client for testing.
                    When provided, Tier-3 is always attempted (overrides env flag).

    Returns:
        A fully populated NormalizedListing.
    """
    # ------------------------------------------------------------------
    # Tier 1: structured field lift
    # ------------------------------------------------------------------
    fields = tier1.extract_structured(raw)

    # Internal key used only within this function; not part of schema.
    location_raw_value: str | None = fields.pop("location_raw_value", None)

    # ------------------------------------------------------------------
    # Tier 2: regex extraction
    # ------------------------------------------------------------------
    description: str = fields.get("description_raw") or ""

    # Rate
    rate = tier2.parse_rate(raw.salary_raw, description)
    fields.update(rate)

    # IR35
    ir35_val = tier2.parse_ir35(description)
    fields["ir35_status"] = ir35_val  # None = "not_stated" (Polly-compat)

    # Duration — prefer metadata["duration_raw"] (set by adapter) over regex
    meta_duration: str | None = (
        raw.metadata.get("duration_raw") if raw.metadata else None
    )
    dur_text = meta_duration or description
    dur_phrase, dur_weeks = tier2.parse_duration(dur_text)
    fields["duration_raw"] = dur_phrase or meta_duration
    fields["duration_weeks"] = dur_weeks

    # ASAP flag
    fields["asap_flag"] = tier2.parse_asap(description)

    # Start date
    start_raw = tier2.parse_start_date_raw(description)
    fields["start_date_raw"] = start_raw
    if start_raw and start_raw.lower() in ("asap", "immediately", "immediate"):
        fields["asap_flag"] = True
        fields["start_date"] = None
    elif start_raw:
        fields["start_date"] = tier2.parse_start_date(start_raw)

    # Contract type
    fields["contract_type"] = tier2.parse_contract_type(
        raw.contract_type_raw,
        title=raw.title,
        description=description,
    )

    # Remote policy
    combined_for_remote = description + " " + (raw.location_raw or "")
    fields["remote_policy"] = tier2.parse_remote_policy(combined_for_remote)

    # Location normalisation
    fields["location_normalized"] = tier2.normalize_location(location_raw_value)
    fields["country"] = tier2.detect_country(location_raw_value)

    # Sector
    fields["sector"] = sector_mod.assign_sector(raw)

    # ------------------------------------------------------------------
    # Tier 3: LLM fallback (only when enabled or a test client is injected)
    # ------------------------------------------------------------------
    if ENABLE_LLM_FALLBACK or llm_client is not None:
        _apply_llm_fallback(fields, raw, description, llm_client)

    return NormalizedListing(**fields)


def _apply_llm_fallback(
    fields: dict,
    raw: RawListing,
    description: str,
    llm_client: object | None,
) -> None:
    """Fill remaining gaps using the LLM tier.  Mutates *fields* in-place."""
    slid = raw.source_listing_id

    # Rate fallback
    if fields.get("day_rate_min") is None and description:
        rate_data, conf = tier3.extract_rate_from_prose(description, llm_client)
        if rate_data and conf >= 0.6:
            fields.setdefault("day_rate_min", rate_data.get("min"))
            fields.setdefault("day_rate_max", rate_data.get("max"))
            fields.setdefault("rate_period", rate_data.get("period", "day"))
            fields.setdefault("rate_currency", rate_data.get("currency", "GBP"))
            logger.debug("LLM rate fallback used for %s (conf=%.2f)", slid, conf)

    # Start-date fallback
    if fields.get("start_date") is None and not fields.get("asap_flag") and description:
        date_str, conf = tier3.extract_date_from_prose(description, llm_client)
        if date_str and conf >= 0.6:
            if date_str.lower() in ("asap", "immediately", "immediate"):
                fields["asap_flag"] = True
            else:
                fields["start_date_raw"] = fields.get("start_date_raw") or date_str
                fields["start_date"] = tier2.parse_start_date(date_str)
            logger.debug("LLM date fallback used for %s", slid)

    # IR35 fallback — upgrade "not_stated" to "undetermined" when body has signal
    if fields.get("ir35_status") is None and description:
        ir35_val, conf = tier3.extract_ir35_from_body(description, llm_client)
        if ir35_val and conf >= 0.6:
            fields["ir35_status"] = ir35_val
            logger.debug("LLM IR35 fallback used for %s", slid)

    # Location disambiguation
    if not fields.get("location_normalized") and fields.get("location"):
        loc_val, conf = tier3.disambiguate_location(
            fields["location"], description, llm_client
        )
        if loc_val and conf >= 0.7:
            fields["location_normalized"] = tier2.normalize_location(loc_val)
            logger.debug("LLM location fallback used for %s", slid)
