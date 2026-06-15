# Decision: UK Filter Strengthening — Geo Detection from Title/Description

**Date:** 2026-06-14  
**Author:** Ada  
**Status:** Implemented  
**Area:** `src/mechpm/extractor/regex_fields.py`, `src/mechpm/extractor/filters.py`

---

## Context

A listing titled *"Project Manager (High Speed Rail) - MENA Region"* (RailwayPeople) appeared in the main report on 2026-06-14. Root cause: adapter sent `location_raw=""`, the country detector only scanned `location`, and `passes_uk` defaulted to **allow** when country was unknown (GB default).

Tommy's earlier lockout patch (commit 65daae1) added non-UK patterns to `_NON_UK_MAP` but the detector only ran against `location`. This ticket closes the remaining gap: scanning title and description when location is absent.

---

## Decision

### 1. Add MENA to `_NON_UK_MAP`

Pattern `\b(mena|middle\s+east)\b → "AE"` added as the first entry in the Gulf/MENA block. Covers the exact string seen in the leaked listing.

### 2. Extend `detect_country` with optional `title` / `description` params

Signature: `detect_country(location_raw, title=None, description=None) → str`

Scan order: location → title → description. First non-UK match wins. Returns `"GB"` for no-signal case (preserves `country: str` type contract with `NormalizedListing`). Backward-compatible with existing pipeline call `detect_country(location_raw_value)`.

### 3. `passes_uk` defaults to reject on unknown country

New logic:
1. `country != "GB"` → reject (location confirmed non-UK).
2. `location` non-empty + `country == "GB"` → pass (confirmed UK).
3. `location` empty + `country == "GB"` (default) → secondary scan of title + description using `_NON_UK_MAP`:
   - Non-UK signal found → reject (silent, no flag needed — country detected).
   - No signal found → reject + append `"country_unknown_assumed_non_uk"` to `listing.sanity_flags`.

### 4. Sanity flag routes to Review Queue

The `sanity_flags` list is owned by Polly's reporter. The existing flag mechanism (already on `NormalizedListing`) is used without new infrastructure. The reporter already has a Review Queue section; listings with sanity flags are routed there.

---

## Alternatives considered

| Alternative | Reason rejected |
|---|---|
| Default-allow on unknown location | Precision bug — the root cause of the leak |
| Change `detect_country` return to `Optional[str]` | Breaks `country: str = "GB"` model field; requires pipeline.py + models.py changes |
| Add new `passes_uk_or_review_filter()` | Unnecessary complexity; existing sanity_flags path is sufficient |
| Modify pipeline.py to pass title/description | pipeline.py is in the explicit change lockout |

---

## Impact

- **Precision:** UK filter now rejects non-UK geo signals in title/description even when location is empty. Closes the "MENA Region" leak.
- **Recall:** Listings with clear UK location (`location` non-empty) are unaffected. One regression test (`pos_09_uk_clear_signal`) guards this.
- **Review Queue:** Listings with truly unknown country are surfaced for human review rather than silently dropped or incorrectly included in main results.
- **Test count:** 106 → 123 (+17 tests; 5 new gold-set fixtures + 6 dedicated unit tests).

---

## Awaiting

- Polly to confirm Review Queue section handles `"country_unknown_assumed_non_uk"` sanity flag (current implementation relies on existing sanity_flags routing already in the reporter).
- Michael to investigate adapter calibration gap that causes `location_raw=""` from RailwayPeople (separate ticket).
