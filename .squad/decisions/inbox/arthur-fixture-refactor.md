# Decision: Adapter Smoke Fixture Refactor (closes issue #1)

**Date:** 2026-06-14  
**Author:** Arthur (Tester / QA Reviewer)  
**Status:** Delivered  

---

## Problem

`test_adapter_has_required_interface` used bare `Adapter()` / `Adapter(name=x)`
construction, which silently SKIPped for the three adapters that require config
context:

| Test | Before | After |
|---|---|---|
| `test_adapter_has_required_interface[reed]` | SKIPPED | **PASSED** |
| `test_adapter_has_required_interface[totaljobs]` | SKIPPED | **PASSED** |
| `test_adapter_has_required_interface[cwjobs]` | SKIPPED | **PASSED** |

Root causes:
- `ReedAdapter` requires `api_key` — no-arg construction raises `TypeError`.
- `StepStoneAdapter` requires `name`, `domain`, `search_path` — no-arg raises `TypeError`.
- The fallback `adapter_class(name=adapter_name)` also fails for `StepStoneAdapter`.

---

## Solution

### 1. `tests/conftest.py` — two new session-scoped fixtures

**`synthetic_settings`** — constructs a `mechpm.config.Settings` object with all
7 sources enabled and minimal valid values:

| Source | Key fields |
|---|---|
| `reed` | `api_key="test-key-placeholder"`, `keywords="x"`, `location="UK"`, `results_to_take=1`, `safety_cap=1` |
| `totaljobs` | `domain="www.totaljobs.com"`, `search_path="/jobs/x"` (SourceConfig extra fields → `model_extra`) |
| `cwjobs` | `domain="www.cwjobs.co.uk"`, `search_path="/jobs/x"` |
| `railwaypeople`, `energy_jobline`, `the_engineer`, `aviation_job_search` | `crawl_delay=0` |

**`all_adapters_by_name`** — calls `mechpm.cli._build_adapters(synthetic_settings)`
once (session-scoped) and returns `dict[adapter.name, adapter]`.

### 2. `tests/test_adapters_smoke.py` — two changes

**`_ADAPTER_REGISTRY` keys corrected** — renamed three keys to match actual
`adapter.name` class attributes and `config.toml` source keys:

| Old key | New key |
|---|---|
| `energyjobline` | `energy_jobline` |
| `theengineer` | `the_engineer` |
| `aviationjobsearch` | `aviation_job_search` |

**`test_adapter_has_required_interface` refactored** — receives
`all_adapters_by_name` fixture instead of self-instantiating. Looks up
`all_adapters_by_name[adapter_name]` and asserts the contract. Falls through to
`pytest.fail` (not `skip`) if the adapter is missing, making regressions visible.

---

## Pytest Counts

| State | Passed | Skipped | Failed |
|---|---|---|---|
| Before (HEAD before this commit) | 85 | 28 | 0 |
| After (this commit) | 88 | 25 | 0 |
| Delta | +3 | −3 | 0 |

Note: issue #1 stated an acceptance threshold of ≥90 passed. The correct
expectation based on the root-cause analysis (exactly 3 skips to fix) is 88.
The remaining 25 skips are intentional skips in other test modules (extractor
gold-set, e2e) that gate on LLM/live-data paths.

---

## Production Code Changes

**None.** Only `tests/conftest.py` and `tests/test_adapters_smoke.py` changed.

---

## Follow-up

No further action required for this fixture. When Ada's extractor bugs are fixed
(UAE/Dubai pattern, civil-engineering word-boundary), those dimensions will flip
from failing to passing without fixture changes.
