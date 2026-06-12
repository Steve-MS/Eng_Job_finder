"""RunMetadata: metadata captured for one pipeline execution."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class RunMetadata(BaseModel):
    """Records provenance and quality metrics for a single pipeline run.

    Populated by the orchestrator before calling ``render_weekly``.  All
    ``total_*`` counters must be set by the time the renderer is invoked so the
    header and summary blocks are accurate.
    """

    run_started_at: datetime
    run_finished_at: datetime

    # The calendar window this report covers (inclusive on both ends).
    date_range_start: date
    date_range_end: date

    # Source tracking — failed sources are still shown in the header so Steve
    # can see gaps in coverage at a glance.
    sources_attempted: list[str] = Field(default_factory=list)
    sources_succeeded: list[str] = Field(default_factory=list)
    sources_failed: dict[str, str] = Field(default_factory=dict)  # name → error reason

    # Pipeline count metrics.
    total_raw: int = 0
    total_after_filter: int = 0
    total_after_dedup: int = 0
    total_new: int = 0
    total_urgent: int = 0
    total_sanity_flagged: int = 0
