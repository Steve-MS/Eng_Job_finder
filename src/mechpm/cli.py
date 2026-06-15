"""CLI entry point.

Usage:
    python -m mechpm.cli run-all
    python -m mechpm.cli run-all --source reed
    python -m mechpm.cli run-all --skip-fetch
    python -m mechpm.cli run-all --since 2026-06-07 --skip-report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from mechpm.config import Settings
from mechpm.orchestrator import run_all
from mechpm.pipeline import process_and_report

# Registry maps source name → factory function.
# Add an entry here when a new adapter ships.
_ADAPTER_REGISTRY: dict[str, object] = {}


def _get_registry() -> dict:
    """Lazily build adapter registry (avoids circular imports at module load)."""
    if not _ADAPTER_REGISTRY:
        from mechpm.adapters.reed import ReedAdapter
        from mechpm.adapters.energy_jobline import EnergyJoblineAdapter
        from mechpm.adapters.railwaypeople import RailwayPeopleAdapter
        from mechpm.adapters.aviation_job_search import AviationJobSearchAdapter
        from mechpm.adapters.the_engineer import TheEngineerAdapter
        from mechpm.adapters.stepstone import StepStoneAdapter
        from mechpm.adapters.adzuna import AdzunaAdapter
        _ADAPTER_REGISTRY["reed"] = ReedAdapter
        _ADAPTER_REGISTRY["energy_jobline"] = EnergyJoblineAdapter
        _ADAPTER_REGISTRY["railwaypeople"] = RailwayPeopleAdapter
        _ADAPTER_REGISTRY["aviation_job_search"] = AviationJobSearchAdapter
        _ADAPTER_REGISTRY["the_engineer"] = TheEngineerAdapter
        _ADAPTER_REGISTRY["totaljobs"] = StepStoneAdapter
        _ADAPTER_REGISTRY["cwjobs"] = StepStoneAdapter
        _ADAPTER_REGISTRY["adzuna"] = AdzunaAdapter
    return _ADAPTER_REGISTRY


def _build_adapters(settings: Settings, source_filter: str | None = None):
    """Instantiate enabled adapters from config, optionally filtered to one source."""
    registry = _get_registry()
    log = logging.getLogger("mechpm.cli")
    adapters = []

    for name, cfg in settings.sources.items():
        if not cfg.enabled:
            continue
        if source_filter and name != source_filter:
            continue

        cls = registry.get(name)
        if cls is None:
            log.warning("No adapter registered for source '%s' — skipping.", name)
            continue

        if name == "reed":
            adapters.append(
                cls(  # type: ignore[call-arg]
                    api_key=settings.reed_api_key,
                    crawl_delay=cfg.crawl_delay,
                    keywords=cfg.keywords,
                    location=cfg.location,
                    results_to_take=cfg.results_to_take,
                    safety_cap=cfg.safety_cap,
                )
            )
        elif name in ("totaljobs", "cwjobs"):
            extra: dict = (cfg.model_extra or {}) if cfg.model_extra is not None else {}
            domain = extra.get("domain", "")
            search_path = extra.get("search_path", "")
            if not domain or not search_path:
                log.warning(
                    "Source '%s' missing 'domain' or 'search_path' in config.toml — skipping.",
                    name,
                )
                continue
            adapters.append(
                cls(  # type: ignore[call-arg]
                    name=name,
                    domain=domain,
                    search_path=search_path,
                    crawl_delay=cfg.crawl_delay,
                )
            )
        elif name == "adzuna":
            app_id = os.environ.get("ADZUNA_APP_ID", "")
            app_key = os.environ.get("ADZUNA_APP_KEY", "")
            if not app_id or not app_key:
                log.warning(
                    "ADZUNA_APP_ID or ADZUNA_APP_KEY not set in environment — "
                    "skipping Adzuna source."
                )
                continue
            extra_az: dict = (cfg.model_extra or {}) if cfg.model_extra is not None else {}
            adapters.append(
                cls(  # type: ignore[call-arg]
                    app_id=app_id,
                    app_key=app_key,
                    crawl_delay=cfg.crawl_delay,
                    keywords=cfg.keywords,
                    country=extra_az.get("country", "gb"),
                    results_per_page=extra_az.get("results_per_page", 50),
                    safety_cap=cfg.safety_cap,
                )
            )
        else:
            adapters.append(cls(crawl_delay=cfg.crawl_delay))  # type: ignore[call-arg]

    return adapters


def cmd_run_all(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    settings = Settings.load()
    today = date.today().isoformat()
    skip_fetch: bool = getattr(args, "skip_fetch", False)
    skip_report: bool = getattr(args, "skip_report", False)
    source_filter: str | None = getattr(args, "source", None)

    # Parse --since (default: 7 days ago).
    since_raw: str | None = getattr(args, "since", None)
    if since_raw:
        try:
            since_date = date.fromisoformat(since_raw)
        except ValueError:
            print(f"--since must be YYYY-MM-DD, got '{since_raw}'", file=sys.stderr)
            sys.exit(1)
    else:
        since_date = date.today() - timedelta(days=7)

    manifest: dict | None = None

    if not skip_fetch:
        adapters = _build_adapters(settings, source_filter=source_filter)
        if not adapters:
            print(
                "No enabled adapters found. "
                "Check config.toml (enabled = true) and --source flag.",
                file=sys.stderr,
            )
            sys.exit(1)

        results = asyncio.run(run_all(adapters))
        total = sum(len(v) for v in results.values())
        print(f"\nFetch complete — {total} listing(s) across {len(results)} source(s).")
        for source, listings in results.items():
            print(f"  {source}: {len(listings)} listing(s)")

        # Read the manifest that run_all just wrote.
        manifest_path = Path("data/raw") / today / "run_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    else:
        print(f"--skip-fetch: processing existing JSONL for {today}")
        manifest_path = Path("data/raw") / today / "run_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    pipeline_result = process_and_report(
        date_str=today,
        since_date=since_date,
        skip_report=skip_report,
        manifest=manifest,
    )

    print(f"\nPipeline result:")
    print(f"  fetched:      {pipeline_result.fetched}")
    print(f"  extracted:    {pipeline_result.extracted}")
    print(f"  quarantined:  {pipeline_result.quarantined}")
    print(f"  filtered_out: {pipeline_result.filtered_out}")
    print(f"  deduped:      {pipeline_result.deduped}")
    print(f"  stored:       {pipeline_result.stored}")
    print(f"  reported:     {pipeline_result.reported}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mechpm",
        description="Mech-PM-Finder — UK mechanical-engineering PM contract job scanner",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run-all",
        help="Full pipeline: fetch → extract → filter → dedup → store → report",
    )
    run_parser.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help="Run a single named adapter only (e.g. 'reed')",
    )
    run_parser.add_argument(
        "--skip-fetch",
        action="store_true",
        default=False,
        help="Skip fetching; process existing JSONL for today's date.",
    )
    run_parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        default=None,
        help="Report window start date (default: 7 days ago). ISO date.",
    )
    run_parser.add_argument(
        "--skip-report",
        action="store_true",
        default=False,
        help="Stop after storage; skip report generation.",
    )
    run_parser.set_defaults(func=cmd_run_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
