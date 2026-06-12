# Mech-PM-Finder — Test Suite README
> Date: 2026-06-12 | Owner: Arthur (QA)

---

## Overview

This directory contains the full test harness for `mech-pm-finder`, including:

| File / Directory | Purpose |
|---|---|
| `conftest.py` | Shared fixtures, path bootstrap, metrics collector |
| `fixtures/gold_set/` | 25 hand-crafted listing fixtures (see below) |
| `test_extractor.py` | Extraction accuracy tests against gold set |
| `test_filters.py` | Filter precision/recall tests against gold set |
| `test_dedup.py` | Deduplication quality tests |
| `test_adapters_smoke.py` | Adapter interface + Reed-specific mock tests |
| `test_e2e.py` | End-to-end pipeline tests (marked `@pytest.mark.slow`) |
| `test_report.py` | Report rendering quality tests |
| `acceptance_gate.py` | Standalone scorecard runner for release gating |
| `.test_metrics.json` | **Generated** — session metrics written by conftest hook |
| `acceptance_report.json` | **Generated** — machine-readable scorecard from acceptance gate |

---

## Running the Tests

```bash
# Fast unit tests (no E2E, no network)
pytest tests/ -m "not slow" -v

# All tests including E2E
pytest tests/ -v

# Acceptance gate (run tests + compute scorecard)
python tests/acceptance_gate.py

# Acceptance gate — skip re-running tests (read existing metrics)
python tests/acceptance_gate.py --skip-tests

# Acceptance gate — include E2E slow tests
python tests/acceptance_gate.py --include-slow

# Just the filter threshold tests
pytest tests/test_filters.py -v
```

---

## Gold Set Structure

```
tests/fixtures/gold_set/
├── positive/          # 8 true-positive listings (contract/UK/PM/mech-eng)
├── negative/          # 8 true-negative listings (exactly one filter fails each)
├── edge_cases/        # 6 edge cases testing borderline extraction scenarios
└── duplicate_pairs/   # 3 cross-posted pairs for dedup testing
```

Each category has **raw fixture files** (`*.json`) and **sibling expected files** (`*.expected.json` or `*.dedup_expected.json`).

### Raw Fixture Format (`RawListing`)

```json
{
  "source": "reed",
  "url": "https://...",
  "source_listing_id": "12345678",
  "title": "Contract Project Manager – Rail Infrastructure",
  "employer": null,
  "agency": "Rullion",
  "location_raw": "Leeds, West Yorkshire",
  "posted_at": "2026-06-10T09:00:00+01:00",
  "contract_type_raw": "Contract",
  "salary_raw": "£700 per day",
  "description_raw": "Full job description text...",
  "metadata": {
    "duration_raw": "6 months"
  }
}
```

> **`metadata.duration_raw`**: Add this when the contract duration is mentioned **after** experience requirements in the description text. The extractor reads `metadata.duration_raw` first to avoid picking up the experience-year figure instead of the contract length.

### Expected Fixture Format (`.expected.json`)

```json
{
  "extracted": {
    "country": "GB",
    "location_normalized": "Leeds",
    "day_rate_min": 700.0,
    "day_rate_max": null,
    "rate_currency": "GBP",
    "rate_period": "day",
    "ir35_status": "outside",
    "contract_type": "contract",
    "duration_weeks": 24,
    "start_date": "2026-07-14",
    "asap_flag": false,
    "sector": "rail"
  },
  "filters": {
    "contract_filter": true,
    "uk_filter": true,
    "pm_filter": true,
    "mech_filter": true,
    "overall_pass": true
  },
  "meta": {
    "category": "positive",
    "sector": "rail",
    "difficulty": "structured",
    "notes": "Human notes on what makes this fixture interesting."
  }
}
```

> **`day_rate_max`**: Set to `null` for single-rate listings (e.g. "£700 per day"). The extractor only populates `day_rate_max` when a range is present (e.g. "£520–£580 per day").
> **`duration_weeks`**: The extractor multiplies months × 4 and years × 52. For a 6-month contract: `6 × 4 = 24 weeks`.

### Duplicate Pair Expected Format (`.dedup_expected.json`)

```json
{
  "pair_id": "dup_01",
  "files": ["dup_01a.json", "dup_01b.json"],
  "should_merge": true,
  "merge_signals": {
    "title_jw_similarity": 0.87,
    "same_agency": true,
    "same_location_bucket": "Leeds",
    "rate_overlap_pct": 100.0,
    "days_between_posts": 1
  },
  "canonical_source": "reed",
  "canonical_fields": { ... }
}
```

---

## Gold Set Composition

### Positives (8) — all filters pass
| File | Sector | Source | Rate | IR35 |
|---|---|---|---|---|
| `pos_01_rail_pm` | Rail | Reed | £700 | Outside |
| `pos_02_aero_pm` | Aerospace | Aviation Job Search | £650 | Outside |
| `pos_03_energy_pm` | Energy | Energy Jobline | £800 | Outside (ASAP start) |
| `pos_04_mne_pm` | Construction M&E | Totaljobs | £550 | Inside |
| `pos_05_generalist_pm` | Process | CWJobs | £520–580 | Undetermined |
| `pos_06_defence_pm` | Defence | CWJobs | £750 | Outside |
| `pos_07_automotive_pm` | Automotive | Reed | £580 | Outside |
| `pos_08_nuclear_pm` | Nuclear | Totaljobs | £725 | Outside |

### Negatives (8) — exactly one filter fails each
| File | Fail Filter | Why |
|---|---|---|
| `neg_01_perm_rail` | contract_filter | Permanent Network Rail job |
| `neg_02_perm_energy` | contract_filter | Permanent Aker Solutions job |
| `neg_03_nonuk_dubai` | uk_filter | Dubai, UAE (AED rate) |
| `neg_04_nonuk_germany` | uk_filter | Munich, Germany (EUR rate) |
| `neg_05_engineer` | pm_filter | Mechanical Engineer (not PM) |
| `neg_06_draughtsman` | pm_filter | CAD Draughtsman (not PM) |
| `neg_07_civil_pm` | mech_filter | Highways/civil PM only |
| `neg_08_software_pm` | mech_filter | Software development PM |

### Edge Cases (6)
| File | Challenge | Expected Outcome |
|---|---|---|
| `edge_01_ambiguous_title` | Title = "Project Manager" only | PASS — mech+PM signals in body |
| `edge_02_rate_buried` | Rate only in prose words | PASS — LLM fallback extracts rate |
| `edge_03_ir35_body_only` | IR35 status only in description | PASS — regex finds "outside IR35" |
| `edge_04_region_ambiguity` | Location = "Midlands" | PASS — resolves to GB |
| `edge_05_asap_date` | Start = "ASAP" | PASS — asap_flag=true, start_date=null |
| `edge_06_multi_discipline` | Mech + Civil combined | PASS — mechanical is primary scope |

### Duplicate Pairs (3 × 2 = 6 files)
| Pair | Sources | Signal |
|---|---|---|
| `dup_01` a/b | Reed ↔ Totaljobs | Rullion, Leeds, Rail PM |
| `dup_02` a/b | Reed ↔ Energy Jobline | Brunel, Aberdeen, Offshore PM |
| `dup_03` a/b | CWJobs ↔ Aviation Job Search | Matchtech, Bristol, Aero PM |

---

## Acceptance Thresholds

These are the thresholds checked by `acceptance_gate.py`. All are **precision-first** (false positives are more harmful than false negatives).

| Dimension | Min Precision | Min Recall | Rationale |
|---|---|---|---|
| **Adapter** | 100% pass rate | — | Adapters must not silently fail |
| **Extraction** | ≥ 95% | — | Structured fields must be reliable |
| **Contract filter** | ≥ 98% | ≥ 92% | No perm jobs must slip through |
| **UK filter** | ≥ 99% | ≥ 95% | No non-UK jobs must slip through |
| **PM filter** | ≥ 92% | ≥ 88% | Title ambiguity makes this harder |
| **Mech filter** | ≥ 96% | ≥ 90% | Must reject civil/software/IT |
| **Dedup** | ≥ 98% | ≥ 88% | False merges lose listings |

---

## Adding New Gold-Set Fixtures

1. Create `tests/fixtures/gold_set/<category>/<name>.json` with the raw listing.
2. Create `tests/fixtures/gold_set/<category>/<name>.expected.json` with:
   - `"extracted"`: the fields you expect the extractor to populate
   - `"filters"`: boolean outcomes for each filter
   - `"meta"`: category, difficulty, notes
3. Run `pytest tests/test_filters.py::test_gold_set_expected_files_present` to validate.
4. Update `test_filters.py::test_gold_set_fixture_counts` if you've changed the total count.

### Guidelines
- **Positives**: must pass ALL four filters. Use varied sectors and agencies.
- **Negatives**: must fail exactly ONE filter. Name the `fail_filter` in meta.
- **Edge cases**: document the `tricky_fields` explicitly in meta. Use `difficulty` values: `structured`, `semi_structured`, `ambiguous_title`, `rate_in_prose`, `ir35_in_body_only`, `vague_location`, `asap_start_date`, `multi_discipline`.
- **Duplicate pairs**: make merge signals realistic (JW ≈ 0.80–0.90, rate within ±£25, same agency).

---

## Interpreting the Scorecard

```
== MECH-PM-FINDER — ACCEPTANCE GATE SCORECARD ============================
  Dimension               Status    Precision     Recall      n
--------------------------------------------------------------------------
  Contract filter         ✅ PASS    0.990 (≥0.98)  0.929 (≥0.92)   28
  UK geo filter           ✅ PASS    1.000 (≥0.99)  0.952 (≥0.95)   28
  PM role filter          ❌ FAIL    0.910 (≥0.92)  0.900 (≥0.88)   28
    → precision 0.910 < 0.920 (TP=10, FP=1)
  ...
--------------------------------------------------------------------------
  ❌  1 DIMENSION(S) FAIL — DO NOT RELEASE
```

- **Precision** = TP / (TP + FP): how many predicted positives are correct.
- **Recall** = TP / (TP + FN): how many actual positives were found.
- **n** = total samples tested for this dimension.
- A dimension shows `n/a` if no tests collected data for it (modules absent).
- The JSON scorecard at `tests/acceptance_report.json` is suitable for CI parsing.

---

## CI Integration

```yaml
# Example GitHub Actions step
- name: Run acceptance gate
  run: python tests/acceptance_gate.py --json-output gate_results.json
  
- name: Upload scorecard
  uses: actions/upload-artifact@v4
  with:
    name: acceptance-scorecard
    path: gate_results.json
```

The script exits with code `0` (pass) or `1` (fail), suitable for gating CI pipelines.

---

## Known Extractor Deficiencies (as at 2026-06-12)

The following gold-set fixtures expose **real bugs** in the current extractor implementation. Arthur's test harness correctly flags these as acceptance-gate failures. They are kept in the gold set deliberately — fixing the extractor (Ada's work) should make them pass.

| Fixture | Failing Test | Root Cause | Extractor Fix Needed |
|---|---|---|---|
| `neg_03_nonuk_dubai` | `test_filter_outcomes`, `test_uk_filter_precision_recall` | `detect_country()` in `regex_fields.py` has no UAE/Dubai pattern — defaults to "GB" | Add `(re.compile(r"\b(dubai|abu dhabi|uae|united arab emirates)\b", ...), "AE")` to `_NON_UK_MAP` |
| `edge_06_multi_discipline` | `test_filter_outcomes` | `passes_mechanical()` checks `"civil engineer"` as a substring of title — "Civil Engineering" triggers false disqualification | Change substring check to word-boundary regex for disqualify phrases in title |

**Impact on acceptance scorecard (current state):**
- `uk_filter` precision: 0.952 (below 0.990 threshold) — FAIL
- `mech_filter` recall: reduced by 1 FN — may push below 0.90 threshold

These failures are by design. When Ada/Michael resolve these extractor bugs, re-running `python tests/acceptance_gate.py` should flip these dimensions from FAIL → PASS.

