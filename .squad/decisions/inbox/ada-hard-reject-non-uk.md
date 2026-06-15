# Decision: Hard-Reject Known Non-UK Countries (No Review Queue)

**Date:** 2026-06-15  
**Author:** Ada (Data Extraction)  
**Triggered by:** Cairo/Egypt listing in Review Queue of 2026-06-15 live report  
**Status:** Implemented

---

## Context

The 2026-06-15 live report contained a listing from ewi Recruitment — *"Project Manager (High Speed Rail) - MENA Region"* — with an explicit location of `"Cairo, Cairo Governorate, Egypt"`. This listing appeared in the Review Queue because:

1. `_NON_UK_MAP` (in `regex_fields.py`) did not include Egypt, Cairo, or Alexandria.
2. `detect_country("Cairo, Cairo Governorate, Egypt")` found no match and fell through to the "location present → confirmed UK" branch, returning `"GB"`.
3. `passes_uk` saw `country="GB"` + non-empty location → returned `True`.
4. The listing entered the Review Queue only because of a **separate** "Day rate missing" sanity flag — the UK filter never fired at all.

This is a precision failure (false positive in the UK-confirmed path).

## Decision

### 1. Extend `_NON_UK_MAP` with Egypt/Cairo

Add an Africa section to `_NON_UK_MAP`:

```python
# Africa
(re.compile(r"\b(egypt|cairo|alexandria)\b", re.IGNORECASE), "EG"),
```

This ensures `detect_country` returns `"EG"` for any Egyptian location string.

### 2. Restructure `passes_uk` with explicit three-way logic

Replace the implicit "country != GB → reject" guard with a documented three-case structure:

| Country state | Location state | Action |
|--------------|---------------|--------|
| In `_KNOWN_NON_UK_CODES` | any | Hard-reject, **no flag** |
| `"GB"` | present | Pass (confirmed UK) |
| `"GB"` | absent, non-UK signal in title/desc | Hard-reject, no flag |
| `"GB"` | absent, no geo signal anywhere | Soft-reject + `country_unknown_assumed_non_uk` flag |

Key principle: **the sanity flag is only for genuinely unknown origin** — not for confirmed non-UK. Routing a known-non-UK listing to the Review Queue wastes Steve's review time.

### 3. `_KNOWN_NON_UK_CODES` derived automatically

```python
_KNOWN_NON_UK_CODES: frozenset[str] = frozenset(code for _, code in _NON_UK_MAP)
```

Adding a new country to `_NON_UK_MAP` automatically extends hard-reject coverage — no separate allow/deny list to maintain.

## Consequence

- Known non-UK listings (EG, US, AE, DE, etc.) are **silently dropped** by the filter layer.
- They never reach the report, not even as Review Queue entries.
- The Review Queue remains reserved for listings with genuinely indeterminate geography.
- `filtered_out` count increases accordingly; this is correct and expected.

## Files changed

- `src/mechpm/extractor/regex_fields.py` — Egypt/Cairo entry in `_NON_UK_MAP`
- `src/mechpm/extractor/filters.py` — `_KNOWN_NON_UK_CODES` + restructured `passes_uk`
- `tests/fixtures/gold_set/negative/non_uk_cairo_explicit.{json,expected.json}`
- `tests/fixtures/gold_set/negative/non_uk_us_explicit.{json,expected.json}`
- `tests/fixtures/gold_set/negative/non_uk_uae_explicit.{json,expected.json}`
- `tests/test_filters.py` — count updated 11→14, 4 new unit tests

## Awaiting

Arthur sign-off (precision gate: uk_filter ≥ 0.99 — still met at 138/0 pass/fail).
