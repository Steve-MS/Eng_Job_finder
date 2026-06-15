# Decision: rate_period-Aware Rendering & Normalisation

**Date:** 2026-06-15  
**Author:** Polly  
**Status:** Implemented  
**Scope:** `reporter/domain.py`, `reporter/grouping.py`, `reporter/render.py`

---

## Problem

Ada's `rate_parser` stores hourly rates in `day_rate_min`/`day_rate_max` with
`rate_period='hour'`.  The reporter had no awareness of `rate_period` — it treated
every stored value as £/day regardless.  Result:

- `£46/hr` displayed as `£46/day` (wrong unit)
- Band comparison: `46 < 0.85 × 350 = 297.5` → labelled `(below typical, South-East)` (wrong band)
- Premium-rate flag, suspicious-low/high thresholds, sort keys, and seniority
  classification all used the raw stored value, producing the same systematic error.

---

## Decision

### Display

`_rate_str()` in `render.py` now appends `/hr` when `listing.rate_period == 'hour'`,
otherwise `/day` (including when `rate_period is None`, to preserve existing behaviour
for all non-rate-parser listings).

### Normalisation convention: 8 hours/day

A single helper `effective_day_rate(listing)` in `domain.py` converts hourly rates
to a day-equivalent for **all comparison purposes** (band classification, premium-rate
gate, sanity thresholds, sort keys, seniority tier).

Multiplier choice: **8 hours**.

Rationale:
- 8 hours is the standard UK contract working day referenced in Gatenby Sanderson,
  Kforce, and Apex market surveys (7.5 h is used for some PAYE/NHS roles but not for
  standard engineering contracts).
- Rounding: £46/hr × 8 = £368/day → junior-band (350–600). £90/hr × 8 = £720/day →
  senior-band + premium-rate ✓. £85/hr × 8 = £680/day → just below £700 premium
  threshold ✓.
- The multiplier is a normalisation convention, never displayed to Steve. The report
  always shows the source-truth unit (£/hr or £/day).

If market evidence later suggests 7.5 h (e.g. persistent mis-classification of PAYE
roles), update `HOURS_PER_CONTRACT_DAY` in `domain.py` — one constant, all comparisons
follow.

### Locations of change

| File | What changed |
|------|--------------|
| `domain.py` | Added `HOURS_PER_CONTRACT_DAY = 8`; added public `effective_day_rate(listing)` helper; `rate_context()` now calls `effective_day_rate` instead of `_effective_rate` |
| `grouping.py` | `is_premium()` uses `effective_day_rate`; `get_sanity_reasons()` uses `effective_day_rate` for suspicious-low (≤250), suspicious-high (≥1500), and IR35-at-high-rate (≥700) checks |
| `render.py` | `_rate_str()` unit suffix; `_classify_seniority()` uses `effective_day_rate`; both sort-key lambdas use `effective_day_rate` |

### Before / after for the two bug-report examples

| Listing | Before | After |
|---------|--------|-------|
| £46/hr, Inside, South-East | `£46/day \| (below typical, South-East)` | `£46/hr \| (junior-band, South-East)` |
| £38–£48/hr, Inside, Region TBC | `£38–£48/day \| (below typical, Region TBC)` | `£38–£48/hr \| (junior-band, Region TBC)` |

Note: £40/hr (= £320/day, South-East) still shows `(below typical, South-East)` —
this is **correct**: 320/1.10 = 290.9 < 0.85 × 350 = 297.5.  The label is accurate.

---

## Test coverage added

`tests/test_rate_period.py` — 32 new tests:

- `TestRateStr` (6): unit suffix display for single/range, hourly/daily/None period  
- `TestEffectiveDayRate` (6): 8× multiplier, period=None default, max-over-min, None→None  
- `TestBanding` (5): £46/hr not "below typical"; junior-band; regression guard daily-46; range; £75/hr mid-band  
- `TestPremium` (5): £90/hr outside✓; £85/hr outside✗; daily boundary; inside✗; exact £87.50/hr boundary  
- `TestSanityRates` (4): £15/hr flagged low; £40/hr not flagged; £200/day still flagged; £200/hr flagged high  
- `TestClassifySeniority` (4): £46/hr→junior, £65/hr→mid, £90/hr→senior, £600/day→mid  
- `TestSortOrder` (2): £600/day > £45/hr; £70/hr > £500/day

Test delta: 366 → 398 passed (all green, 25 skipped unchanged).
