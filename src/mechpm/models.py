"""Canonical domain models shared across the mechpm pipeline.

Polly stub (2026-06-12) extended by Ada (2026-06-12) with the full 29-field
extraction schema.  All Polly-original fields are preserved unchanged so the
reporter never needs updating for Ada's additions.

Schema contract (Ada):
  listing_id  = SHA-256(source|source_listing_id|title|employer)[:16 hex],
                auto-computed when not supplied.
  source_urls = starts as [source_url]; grows when dedup merges cross-board dupes.
  ir35_status = "inside"|"outside"|"undetermined"|"not_stated" (default "not_stated").
                NOTE for Polly: "not_stated" is semantically equivalent to None;
                update sanity checks to include "not_stated" in the unknown-IR35 gate.

Dates: 2026-06-12
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from mechpm.adapters.base import RawListing

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SectorLiteral = Literal[
    "rail",
    "aerospace",
    "defence",
    "energy",
    "nuclear",
    "construction",
    "maritime",
    "automotive",
    "process",
    "generalist",
]

RatePeriodLiteral = Literal["day", "hour"]


class NormalizedListing(BaseModel):
    """Canonical job listing after extraction, filtering, and deduplication.

    Field groups:
      Identity     — listing_id, source, source_listing_id, source_url, source_urls
      Role         — title, employer, agency
      Location     — location, location_normalized, country, remote_policy
      Timing       — posted_at, start_date_raw, start_date, asap_flag
      Duration     — duration_raw, duration_weeks
      Rate         — day_rate_min, day_rate_max, rate_currency, rate_period
      Classification — ir35_status, contract_type, sector
      Content      — description_raw, description_clean
      Provenance   — discovered_at, last_seen_at
      Pipeline     — is_new_listing, sanity_flags
    """

    # ------------------------------------------------------------------
    # Identity (Ada)
    # ------------------------------------------------------------------
    listing_id: str = Field(
        default="",
        description="SHA-256(source|source_listing_id|title|employer)[:16]; auto-computed.",
    )
    source: str                             # adapter name, e.g. "reed", "cwjobs"
    source_listing_id: str = ""            # opaque ID from the source board
    source_url: str = ""                   # canonical URL for this listing
    source_urls: list[str] = Field(default_factory=list)  # grows on dedup merge

    # ------------------------------------------------------------------
    # Role
    # ------------------------------------------------------------------
    title: str
    employer: Optional[str] = None
    agency: Optional[str] = None

    # ------------------------------------------------------------------
    # Location (Polly fields kept; Ada adds country)
    # ------------------------------------------------------------------
    location: str = ""                     # raw location string for display
    location_normalized: str = ""         # lower-cased / postcode-stripped; MUST be str
    country: str = "GB"                   # ISO 3166-1 alpha-2; default GB
    remote_policy: Optional[str] = None   # "remote" | "hybrid" | "on-site"

    # ------------------------------------------------------------------
    # Timing (Ada)
    # ------------------------------------------------------------------
    posted_at: Optional[datetime] = None
    start_date_raw: Optional[str] = None  # verbatim phrase from listing
    start_date: Optional[date] = None
    asap_flag: bool = False

    # ------------------------------------------------------------------
    # Duration (Ada)
    # ------------------------------------------------------------------
    duration_raw: Optional[str] = None    # verbatim phrase, e.g. "6 months"
    duration_weeks: Optional[int] = None  # normalised: months×4, years×52

    # ------------------------------------------------------------------
    # Rate (Ada extends Polly's day_rate_min/max)
    # ------------------------------------------------------------------
    day_rate_min: Optional[float] = None
    day_rate_max: Optional[float] = None
    rate_currency: str = "GBP"
    rate_period: RatePeriodLiteral | None = None  # "day" | "hour"

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    # ir35_status: "inside"|"outside"|"undetermined"|"not_stated"
    # Polly compat: "not_stated" behaves like None for display; update
    # reporter sanity gates (in (None, "undetermined")) to also include
    # "not_stated" in a future Polly sprint.
    ir35_status: Optional[str] = None     # see note above; None == not_stated
    contract_type: str = "contract"
    sector: SectorLiteral = "generalist"  # 10-value enum per Tommy's decision

    # ------------------------------------------------------------------
    # Content (Ada adds description_raw)
    # ------------------------------------------------------------------
    description_raw: Optional[str] = None
    description_clean: Optional[str] = None

    # ------------------------------------------------------------------
    # Provenance (Ada: timezone-aware utcnow)
    # ------------------------------------------------------------------
    discovered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # ------------------------------------------------------------------
    # Pipeline flags (Polly)
    # ------------------------------------------------------------------
    is_new_listing: bool = False           # True when listing_id absent from previous run
    sanity_flags: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Auto-compute listing_id (Ada)
    # ------------------------------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _auto_listing_id_and_urls(cls, values: dict) -> dict:
        """Compute listing_id if not supplied; seed source_urls from source_url."""
        if not values.get("listing_id"):
            payload = "|".join([
                str(values.get("source", "")),
                str(values.get("source_listing_id", "")),
                str(values.get("title", "")),
                str(values.get("employer") or ""),
            ])
            values["listing_id"] = hashlib.sha256(
                payload.encode("utf-8")
            ).hexdigest()[:16]
        if not values.get("source_urls") and values.get("source_url"):
            values["source_urls"] = [values["source_url"]]
        return values

    # ------------------------------------------------------------------
    # Convenience shim (Ada)
    # ------------------------------------------------------------------
    @classmethod
    def from_raw(cls, raw: "RawListing") -> "NormalizedListing":
        """Thin shim → delegates to ``extractor.pipeline.extract()``.

        Prefer ``pipeline.extract()`` directly for batch work to avoid
        repeated lazy-import overhead per call.
        """
        from mechpm.extractor.pipeline import extract  # lazy — avoids circular dep
        return extract(raw)
