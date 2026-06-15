"""Reporter entry point — wraps render_weekly with pipeline integration.

Handles:
  - Querying SQLite for listings since since_date (via Repo.get_listings_since)
  - Reading run_manifest.json for source-status table
  - Marking first-seen-today listings with is_new_listing (🆕)
  - Building RunMetadata from manifest data
  - Calling render_weekly()
  - Always generating a report even when 0 listings pass filters

2026-06-14
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from mechpm.reporter.grouping import is_geo_flagged, is_sanity_flagged, is_urgent
from mechpm.reporter.html_render import render_weekly_html
from mechpm.reporter.models import RunMetadata
from mechpm.reporter.render import render_weekly
from mechpm.storage.sqlite import Repo

logger = logging.getLogger("mechpm.reporter")

_DEFAULT_REPORTS_DIR = Path("reports")


def generate_report(
    repo: Repo,
    date_str: str,
    since_date: date,
    manifest: dict | None,
    reports_dir: Path = _DEFAULT_REPORTS_DIR,
) -> Path:
    """Generate Markdown and HTML reports from SQLite data and run manifest.

    Both formats are written every run:
      - ``reports/{date_str}.md``   — diff-friendly text artefact
      - ``reports/{date_str}.html`` — human-facing browseable view with clickable links

    Args:
        repo:         Open SQLite Repo instance.
        date_str:     The run date (YYYY-MM-DD) — used for 🆕 detection and filename.
        since_date:   Report window start (inclusive); listings with last_seen_at
                      >= since_date are included.
        manifest:     Parsed run_manifest.json, or None when unavailable.
        reports_dir:  Directory to write the report files.

    Returns:
        The resolved path of the written Markdown report file.
    """
    today = date.fromisoformat(date_str)

    listings = repo.get_listings_since(since_date, today=today)

    # Build source tracking from manifest.
    sources_attempted: list[str] = []
    sources_succeeded: list[str] = []
    sources_failed: dict[str, str] = {}
    total_raw = 0

    if manifest:
        for src in manifest.get("sources", []):
            name = src.get("name", "unknown")
            sources_attempted.append(name)
            err = src.get("error")
            if err:
                sources_failed[name] = err
            else:
                sources_succeeded.append(name)
            total_raw += src.get("count", 0)

    now_utc = datetime.now(timezone.utc)
    run_metadata = RunMetadata(
        run_started_at=now_utc,
        run_finished_at=now_utc,
        date_range_start=since_date,
        date_range_end=today,
        sources_attempted=sources_attempted,
        sources_succeeded=sources_succeeded,
        sources_failed=sources_failed,
        total_raw=total_raw,
        total_after_filter=len(listings),
        total_after_dedup=len(listings),
        total_new=sum(1 for l in listings if l.is_new_listing),
        total_urgent=sum(1 for l in listings if is_urgent(l, today)),
        total_sanity_flagged=sum(1 for l in listings if is_geo_flagged(l)),
    )

    output_path = Path(reports_dir) / f"{date_str}.md"
    md_path = render_weekly(listings, run_metadata, output_path)

    html_output_path = Path(reports_dir) / f"{date_str}.html"
    render_weekly_html(listings, run_metadata, html_output_path)
    logger.info("HTML report written: %s", html_output_path)

    return md_path
