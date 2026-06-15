# Decision: JSONL Overwrite Policy + rapidfuzz Core Dep
**Author:** Michael (Backend / Scraping)  
**Date:** 2026-06-15  
**Status:** Implemented and merged  
**Spec:** `.squad/decisions/inbox/tommy-jsonl-policy-and-deps.md`

---

## What was done

### Item 1 — `_persist_jsonl` open mode fixed
`src/mechpm/orchestrator.py` line 98: `open(path, "a", ...)` → `open(path, "w", ...)`.  
Docstring updated from "Append listings" to "Write listings (overwrite)".

### Item 2 — rapidfuzz promoted to required dep
`pyproject.toml` `[project.dependencies]`: added `"rapidfuzz>=3.0"`.  
Installed version: **3.14.5**.

### Item 3 — Stale JSONL files deleted
- `data/raw/2026-06-14/railwaypeople.jsonl` — removed (84,100 bytes, 3× inflated)
- `data/raw/2026-06-15/railwaypeople.jsonl` — removed (100,782 bytes, 2× inflated)

### Item 4 — Regression test
`tests/test_orchestrator.py`: `test_persist_jsonl_overwrites_on_second_run` — calls `_persist_jsonl` twice, asserts line count = 3 (not 6).

### Item 5 — Smoke tests
`tests/test_orchestrator.py`:
- `test_rapidfuzz_importable` — `from rapidfuzz.distance import JaroWinkler` must not raise
- `test_dedup_no_rapidfuzz_warning` — `dedupe_with_groups([])` emits no WARNING

---

## Verification results

| Check | Result |
|---|---|
| pytest count | **131 passed**, 25 skipped, 0 failed |
| rapidfuzz import | `rapidfuzz OK` (v3.14.5) |
| Double-run JSONL size | 50,391 bytes (one run) — NOT 100,782 bytes (two runs). Overwrite confirmed. |
| rapidfuzz WARNING in pipeline | **None** — `Select-String "rapidfuzz"` returns no matches |

---

## Learning recorded

`rapidfuzz` Jaro-Winkler dedup catches ghost duplicates (minor field variations across re-runs) that identity/content-hash dedup misses. Live evidence: `deduped: 6` with rapidfuzz vs `deduped: 0` with identity fallback on the same dataset.
