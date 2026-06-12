"""Smoke test for the report renderer.

Fabricates 5 NormalizedListing instances covering all major code paths and
renders to ``reports/smoke-test-2026-06-12.md``.

Run with:
    python -m mechpm.reporter

The output path is printed on success so it can be opened directly.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Resolve repo root relative to this file so the smoke test works from any cwd.
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # reporter/ → mechpm/ → src/ → repo
sys.path.insert(0, str(_REPO_ROOT / "src"))

from mechpm.models import NormalizedListing
from mechpm.reporter.models import RunMetadata
from mechpm.reporter.render import render_weekly

_TODAY = date(2026, 6, 12)
_NOW = datetime(2026, 6, 12, 17, 5, 0, tzinfo=timezone.utc)


def _listing(**kwargs) -> NormalizedListing:
    """Convenience builder with sensible defaults."""
    defaults = dict(
        listing_id="smoke-001",
        source="reed",
        source_urls=["https://reed.co.uk/jobs/smoke-001"],
        title="Project Manager",
        location="Unknown",
        location_normalized="unknown",
        discovered_at=_NOW,
        last_seen_at=_NOW,
    )
    defaults.update(kwargs)
    return NormalizedListing(**defaults)


def main() -> None:
    listings = [
        # 1 — New + Urgent + Premium, London
        _listing(
            listing_id="smoke-001",
            source="reed",
            source_urls=["https://reed.co.uk/jobs/smoke-001"],
            title="Senior Project Manager, Civil Aerospace",
            employer="Rolls-Royce plc",
            location="Derby, East Midlands",
            location_normalized="derby, east midlands",
            day_rate_min=800.0,
            day_rate_max=825.0,
            ir35_status="outside",
            duration_weeks=48,
            start_date=date(2026, 6, 20),   # 8 days — urgent
            description_clean=(
                "Oversight of integrated supplier network across 40+ aerospace component "
                "suppliers. Responsibility for schedule, quality, and programme integration "
                "on next-generation engine development. Must have aerospace programme "
                "experience (5+ years). Travel to supplier sites ~1 week/month."
            ),
            is_new_listing=True,
        ),
        # 2 — New + Urgent, Midlands, inside IR35
        _listing(
            listing_id="smoke-002",
            source="reed",
            source_urls=["https://reed.co.uk/jobs/smoke-002"],
            title="Project Manager, Compact Equipment Platform",
            employer="JCB",
            location="Staffordshire, Midlands",
            location_normalized="staffordshire, midlands",
            day_rate_min=575.0,
            day_rate_max=595.0,
            ir35_status="inside",
            duration_weeks=32,
            start_date=date(2026, 6, 19),   # 7 days — urgent
            description_clean=(
                "New compact equipment line launch. Factory-based programme with supply "
                "chain acceleration focus. Travel to manufacturing partner sites 1–2 weeks/month."
            ),
            is_new_listing=True,
        ),
        # 3 — New + Premium, Scotland, outside IR35
        _listing(
            listing_id="smoke-003",
            source="cwjobs",
            source_urls=["https://cwjobs.co.uk/jobs/smoke-003"],
            title="Wind Farm Project Manager",
            employer="GE Renewable Energy",
            location="Edinburgh, Scotland",
            location_normalized="edinburgh, scotland",
            day_rate_min=700.0,
            day_rate_max=720.0,
            ir35_status="outside",
            duration_weeks=40,
            start_date=date(2026, 7, 5),    # 23 days — not urgent
            description_clean=(
                "Wind farm construction & commissioning (Scottish Borders). Supplier "
                "coordination, site safety integration. Scottish location; market rate "
                "typically lower than South-East by ~15%."
            ),
            is_new_listing=True,
        ),
        # 4 — Standard (not new, not urgent), North, dedup-merged across two sources
        _listing(
            listing_id="smoke-004",
            source="totaljobs",
            source_urls=[
                "https://totaljobs.com/jobs/smoke-004",
                "https://reed.co.uk/jobs/smoke-004b",
            ],
            title="Programme Manager, Terminal Expansion",
            employer="Manchester Airport",
            location="Manchester, North-West",
            location_normalized="manchester, north west",
            day_rate_min=640.0,
            day_rate_max=670.0,
            ir35_status="outside",
            duration_weeks=56,
            start_date=date(2026, 7, 10),   # 28 days
            description_clean=(
                "Terminal 2 expansion programme. Major infrastructure; site-based "
                "coordination with contractors and local authorities."
            ),
            is_new_listing=False,
        ),
        # 5 — Sanity-flagged: rate below £250 threshold
        _listing(
            listing_id="smoke-005",
            source="cwjobs",
            source_urls=["https://cwjobs.co.uk/jobs/smoke-005"],
            title="Project Coordinator (Contract)",
            employer="Undisclosed Client",
            agency="Generic Recruitment Ltd",
            location="Reading, South-East",
            location_normalized="reading, south-east",
            day_rate_min=180.0,
            day_rate_max=220.0,   # ≤ £250 — triggers sanity flag
            ir35_status="inside",
            duration_weeks=12,
            start_date=date(2026, 7, 1),
            description_clean=(
                "Short-term project coordination support. Umbrella only. "
                "Competitive rate offered."   # also triggers 'competitive rate' red flag
            ),
            is_new_listing=True,
        ),
    ]

    meta = RunMetadata(
        run_started_at=datetime(2026, 6, 12, 16, 55, 0, tzinfo=timezone.utc),
        run_finished_at=_NOW,
        date_range_start=date(2026, 6, 9),
        date_range_end=_TODAY,
        sources_attempted=["reed", "cwjobs", "totaljobs", "railwaypeople", "energy_jobline"],
        sources_succeeded=["reed", "cwjobs", "totaljobs"],
        sources_failed={
            "railwaypeople": "HTTP 503 — site unavailable during run window",
            "energy_jobline": "Crawl-delay exceeded; retrying next run",
        },
        total_raw=38,
        total_after_filter=22,
        total_after_dedup=18,
        total_new=3,        # listings 1–3 (listing 5 is sanity-flagged)
        total_urgent=2,     # listings 1–2
        total_sanity_flagged=1,  # listing 5
    )

    output_path = _REPO_ROOT / "reports" / "smoke-test-2026-06-12.md"
    result = render_weekly(listings, meta, output_path)
    sys.stdout.buffer.write(
        f"[OK] Smoke test passed -- report written to:\n   {result}\n".encode("utf-8")
    )


if __name__ == "__main__":
    main()
