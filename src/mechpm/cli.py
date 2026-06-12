"""CLI entry point.

Usage:
    python -m mechpm.cli run-all
    python -m mechpm.cli run-all --source reed
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from mechpm.config import Settings
from mechpm.orchestrator import run_all

# Registry maps source name → factory function.
# Add an entry here when a new adapter ships.
_ADAPTER_REGISTRY: dict[str, object] = {}


def _get_registry() -> dict:
    """Lazily build adapter registry (avoids circular imports at module load)."""
    if not _ADAPTER_REGISTRY:
        from mechpm.adapters.reed import ReedAdapter
        _ADAPTER_REGISTRY["reed"] = ReedAdapter
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
    adapters = _build_adapters(settings, source_filter=getattr(args, "source", None))

    if not adapters:
        print(
            "No enabled adapters found. "
            "Check config.toml (enabled = true) and --source flag.",
            file=sys.stderr,
        )
        sys.exit(1)

    results = asyncio.run(run_all(adapters))

    total = sum(len(v) for v in results.values())
    print(f"\nRun complete — {total} listing(s) across {len(results)} source(s).")
    for source, listings in results.items():
        print(f"  {source}: {len(listings)} listing(s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mechpm",
        description="Mech-PM-Finder — UK mechanical-engineering PM contract job scanner",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-all", help="Run all enabled source adapters")
    run_parser.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help="Run a single named adapter only (e.g. 'reed')",
    )
    run_parser.set_defaults(func=cmd_run_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
