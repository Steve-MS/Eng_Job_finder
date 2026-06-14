# Decision: Pipeline Wiring Implementation

**Author:** Michael (Backend / Scraping)
**Date:** 2026-06-14
**Status:** Implemented — ready for Scribe to merge into decisions.md
**Implements:** Tommy's pipeline-wiring spec (`tommy-pipeline-wiring-spec.md`)

---

## Summary

Wired the complete `fetch → extract → filter → dedup → store → report` pipeline
as specced by Tommy. All 10 work items delivered in a single commit on main.

---

## Changes Delivered

### 1. `orchestrator.py` — run_manifest.json emission

After all adapters complete, `run_all()` writes:
```
data/raw/{YYYY-MM-DD}/run_manifest.json
```
Format: `{"run_date": "...", "sources": [{"name": "...", "count": N, "duration_ms": N, "error": null|"..."}]}`

### 2. `storage/sqlite.py` — Schema migration + UPSERT

New columns on `normalized_listings`:
- `first_seen_at TEXT` — set on first INSERT, never updated
- `times_seen INTEGER NOT NULL DEFAULT 1` — incremented on every subsequent UPSERT

`_migrate_schema()` is called from `_init_schema()` and uses `PRAGMA table_info` to apply `ALTER TABLE` idempotently. Existing rows have `first_seen_at` backfilled from `discovered_at`.

New method `upsert_normalized()` uses:
```sql
ON CONFLICT(listing_id) DO UPDATE SET last_seen_at=excluded.last_seen_at, times_seen=times_seen+1
```
Preserves `first_seen_at` on conflict. Existing `insert_normalized()` retained for backward compatibility.

New method `get_listings_since(since_date, today)` queries by `last_seen_at >= since_date` and dynamically sets `is_new_listing=True` for rows where `first_seen_at[:10] >= today`.

### 3. `pipeline.py` — New module

```python
@dataclass
class PipelineResult:
    fetched, extracted, quarantined, filtered_out, deduped, stored, reported

def process_and_report(date_str, since_date, skip_report, manifest, ...) -> PipelineResult
```

Flow: read JSONL → extract (quarantine failures) → apply 4 filters → dedup → upsert SQLite → optional report.

### 4. Quarantine persistence

On extraction failure (ValidationError or any exception): appends
`{"raw": {...}, "error": "..."}` to `data/quarantine/{date}/{source}.jsonl`.

### 5. `reporter/generate.py` — New entry point

`generate_report(repo, date_str, since_date, manifest, reports_dir)` wraps `render_weekly()`.
Builds `RunMetadata` from manifest, queries listings from DB, marks 🆕 listings
where `first_seen_at == today`, always generates report (even 0 listings).

`reporter/__init__.py` now exports `generate_report`.

### 6. `cli.py` — New flags + pipeline wiring

`run-all` now does full pipeline. New flags:
- `--skip-fetch` — skip adapter phase, process existing JSONL for today
- `--since YYYY-MM-DD` — report window start (default: 7 days ago)
- `--skip-report` — stop after SQLite upsert

`cmd_run_all()` prints `PipelineResult` summary to stdout.

### 7. `.gitignore` — quarantine added

`data/quarantine/` added to .gitignore.

### 8. `run.bat` — No change needed

Already calls `python -m mechpm.cli run-all %*` — new flags pass through correctly.

---

## Live Run Evidence (2026-06-14, --skip-fetch)

Input: `data/raw/2026-06-14/railwaypeople.jsonl` (50 listings)

| Stage | Count | Notes |
|---|---|---|
| fetched | 50 | All JSONL lines read |
| extracted | 50 | 100% extraction success |
| quarantined | 0 | No failures |
| filtered_out | 47 | Rail-generic listings; expected |
| deduped | 0 | rapidfuzz not installed → identity fallback |
| stored | 3 | 3 genuine mech-PM contracts survived all 4 filters |
| reported | True | `reports/2026-06-14.md` created (2582 bytes) |

---

## Test Gate

- Baseline: 88 passed, 25 skipped
- After: **95 passed, 25 skipped, 0 failed** (+7 passes from `test_pipeline_e2e.py`)

---

## Known Issues / Follow-on

1. **`rapidfuzz` not installed in venv** — dedup is identity fallback. Tommy/Scribe should confirm `rapidfuzz>=3.0` is in `pyproject.toml[tool.hatch.envs.default.dependencies]`.
2. **`times_seen` correctness** — `upsert_normalized()` increments `times_seen` on every run with the same JSONL. If the same adapter is run multiple times per day, `times_seen` will reflect the number of process runs, not the number of scrape dates. This is the expected behaviour per Tommy's spec.
