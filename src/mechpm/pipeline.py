"""Pipeline wiring — extract → filter → dedup → store → report.

Public API:
    process_and_report(date_str, since_date, skip_report, manifest, ...) → PipelineResult

This module reads JSONL files produced by the orchestrator, runs each listing
through the extraction/filter/dedup chain, upserts survivors into SQLite, and
optionally renders a Markdown report.

2026-06-14
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from mechpm.adapters.base import RawListing
from mechpm.extractor.dedup import dedupe_with_groups
from mechpm.extractor.filters import passes_contract, passes_mechanical, passes_pm_role, passes_uk
from mechpm.extractor.pipeline import extract
from mechpm.models import NormalizedListing
from mechpm.reporter.generate import generate_report
from mechpm.storage.sqlite import Repo

logger = logging.getLogger("mechpm.pipeline")

_DATA_ROOT = Path("data/raw")
_DB_PATH = Path("data/mechpm.sqlite")
_REPORTS_DIR = Path("reports")
_QUARANTINE_ROOT = Path("data/quarantine")


@dataclass
class PipelineResult:
    """Counts from a single process_and_report() invocation."""
    fetched: int = 0        # raw RawListing lines read from JSONL
    extracted: int = 0      # listings successfully extracted
    quarantined: int = 0    # listings that failed extraction
    filtered_out: int = 0   # listings rejected by filters
    deduped: int = 0        # listings removed by deduplication
    stored: int = 0         # listings upserted to SQLite
    reported: bool = False  # whether a report was generated


def process_and_report(
    date_str: str,
    since_date: date,
    skip_report: bool = False,
    manifest: dict | None = None,
    raw_dir: Path = _DATA_ROOT,
    db_path: Path = _DB_PATH,
    reports_dir: Path = _REPORTS_DIR,
    quarantine_dir: Path = _QUARANTINE_ROOT,
) -> PipelineResult:
    """Run the extract → filter → dedup → store → report pipeline.

    Reads all *.jsonl files from raw_dir/{date_str}/.
    On extraction failure: quarantines the raw listing and continues.
    On filter rejection: discards silently (DEBUG log).
    Deduplicates survivors, upserts to SQLite, optionally renders report.

    Args:
        date_str:      Run date as YYYY-MM-DD; used to locate JSONL and name report.
        since_date:    Report window start (listings with last_seen_at >= since_date).
        skip_report:   When True, skips the report phase.
        manifest:      Parsed run_manifest.json for source-status in report. If None,
                       the function attempts to read it from raw_dir/{date_str}/.
        raw_dir:       Root for raw JSONL data (default: data/raw).
        db_path:       SQLite file path (default: data/mechpm.sqlite).
        reports_dir:   Report output directory (default: reports).
        quarantine_dir: Quarantine root (default: data/quarantine).

    Returns:
        PipelineResult with per-stage counts.
    """
    result = PipelineResult()
    jsonl_dir = Path(raw_dir) / date_str

    # Read manifest from disk if not supplied.
    if manifest is None:
        manifest_path = jsonl_dir / "run_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Could not read run_manifest.json — report will lack source table.")

    # Collect JSONL files.
    jsonl_files = sorted(jsonl_dir.glob("*.jsonl")) if jsonl_dir.exists() else []
    if not jsonl_files:
        logger.error("No JSONL files found in %s", jsonl_dir)
        return result

    # Quarantine directory for this date.
    qdir = Path(quarantine_dir) / date_str

    # Phase 3a/3b: read, extract, filter.
    survivors: list[NormalizedListing] = []

    for jsonl_path in jsonl_files:
        source_name = jsonl_path.stem
        _process_jsonl(
            jsonl_path=jsonl_path,
            source_name=source_name,
            result=result,
            survivors=survivors,
            qdir=qdir,
        )

    logger.info(
        "Process phase: fetched=%d extracted=%d quarantined=%d filtered_out=%d",
        result.fetched,
        result.extracted,
        result.quarantined,
        result.filtered_out,
    )

    # Phase 3c: deduplicate.
    before_dedup = len(survivors)
    if survivors:
        dedup_result = dedupe_with_groups(survivors)
        survivors = dedup_result.listings
    result.deduped = before_dedup - len(survivors)

    # Phase 3d: upsert into SQLite.
    repo = Repo(db_path)
    try:
        if survivors:
            result.stored = repo.upsert_normalized(survivors)
            if dedup_result.groups:
                repo.insert_dedup_groups(dedup_result.groups)
    except Exception:
        logger.exception("SQLite write failed — aborting pipeline.")
        repo.close()
        raise

    # Phase 4: report.
    if not skip_report:
        try:
            generate_report(
                repo=repo,
                date_str=date_str,
                since_date=since_date,
                manifest=manifest,
                reports_dir=Path(reports_dir),
            )
            result.reported = True
            logger.info("Report written to %s/%s.md", reports_dir, date_str)
        except Exception:
            logger.exception("Report render failed — raw data is safe in SQLite.")

    repo.close()
    return result


def _process_jsonl(
    jsonl_path: Path,
    source_name: str,
    result: PipelineResult,
    survivors: list[NormalizedListing],
    qdir: Path,
) -> None:
    """Read one JSONL file, extract, filter, and append survivors in-place."""
    try:
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        logger.exception("Could not read %s — skipping.", jsonl_path)
        return

    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue

        result.fetched += 1

        # Deserialise RawListing.
        raw: RawListing | None = None
        try:
            raw = RawListing.model_validate_json(line)
        except (ValidationError, Exception) as exc:
            logger.warning(
                "Deserialization failed for %s line %d: %s",
                source_name,
                line_num,
                exc,
            )
            _quarantine(qdir, source_name, line, str(exc))
            result.quarantined += 1
            continue

        # Extract NormalizedListing.
        try:
            normalized = extract(raw)
        except (ValidationError, Exception) as exc:
            logger.warning(
                "Extraction failed for %s [%s]: %s",
                source_name,
                raw.source_listing_id,
                exc,
            )
            _quarantine(qdir, source_name, raw.model_dump_json(), str(exc))
            result.quarantined += 1
            continue

        result.extracted += 1

        # Apply four filters (cheapest first).
        if not passes_contract(normalized):
            logger.debug("Filtered (contract): %s", normalized.listing_id)
            result.filtered_out += 1
            continue
        if not passes_uk(normalized):
            logger.debug("Filtered (uk): %s", normalized.listing_id)
            result.filtered_out += 1
            continue
        if not passes_pm_role(normalized):
            logger.debug("Filtered (pm_role): %s", normalized.listing_id)
            result.filtered_out += 1
            continue
        if not passes_mechanical(normalized):
            logger.debug("Filtered (mechanical): %s", normalized.listing_id)
            result.filtered_out += 1
            continue

        survivors.append(normalized)


def _quarantine(qdir: Path, source: str, raw_json: str, error: str) -> None:
    """Append failed listing + error to quarantine JSONL."""
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        qfile = qdir / f"{source}.jsonl"
        entry = json.dumps({"raw": json.loads(raw_json), "error": error})
        with open(qfile, "a", encoding="utf-8") as fh:
            fh.write(entry + "\n")
    except Exception:
        logger.exception("Could not write to quarantine file for source '%s'.", source)
