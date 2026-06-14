"""Orchestrator: runs adapters sequentially and persists raw listings as JSONL."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.orchestrator")

DATA_ROOT = Path("data/raw")


async def run_all(
    adapters: list[SourceAdapter],
    since: datetime | None = None,
) -> dict[str, list[RawListing]]:
    """Run all adapters sequentially, persist JSONL, return results keyed by source name.

    After each adapter (except the last) the orchestrator sleeps
    ``adapter.crawl_delay`` seconds before starting the next one.

    Failures are isolated per-source: a crashed adapter does not prevent
    subsequent adapters from running.
    """
    results: dict[str, list[RawListing]] = {}
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = DATA_ROOT / run_date
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_sources: list[dict[str, Any]] = []

    for idx, adapter in enumerate(adapters):
        t0 = time.monotonic()
        listings: list[RawListing] = []
        error_msg: str | None = None

        try:
            listings = await adapter.fetch(since=since)
        except Exception as exc:
            # Adapters should catch internally, but guard here too.
            error_msg = str(exc)
            logger.warning(
                "Adapter '%s' raised an unexpected exception (should be caught internally): %s",
                adapter.name,
                exc,
                exc_info=True,
            )

        duration_ms = int((time.monotonic() - t0) * 1000)

        summary: dict[str, Any] = {
            "source": adapter.name,
            "count": len(listings),
            "duration_ms": duration_ms,
        }
        if error_msg:
            summary["error"] = error_msg
        logger.info("run_summary %s", json.dumps(summary))

        manifest_sources.append({
            "name": adapter.name,
            "count": len(listings),
            "duration_ms": duration_ms,
            "error": error_msg,
        })

        results[adapter.name] = listings
        _persist_jsonl(out_dir, adapter.name, listings)

        if idx < len(adapters) - 1 and adapter.crawl_delay > 0:
            await asyncio.sleep(adapter.crawl_delay)

    _write_manifest(out_dir, run_date, manifest_sources)
    return results


def _write_manifest(out_dir: Path, run_date: str, sources: list[dict[str, Any]]) -> None:
    """Write run_manifest.json summarising per-source fetch outcomes."""
    manifest = {"run_date": run_date, "sources": sources}
    manifest_path = out_dir / "run_manifest.json"
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.debug("run_manifest written to %s", manifest_path)
    except Exception:
        logger.exception("Failed to write run_manifest.json")


def _persist_jsonl(out_dir: Path, source: str, listings: list[RawListing]) -> None:
    """Append listings to data/raw/{date}/{source}.jsonl (creates file if absent)."""
    path = out_dir / f"{source}.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for listing in listings:
                fh.write(listing.model_dump_json() + "\n")
        logger.debug("Persisted %d listing(s) to %s", len(listings), path)
    except Exception:
        logger.exception("Failed to persist listings for source '%s'.", source)
