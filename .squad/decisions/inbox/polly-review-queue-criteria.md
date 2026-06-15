# Decision: Review Queue Routing Criteria (Polly)

**Date:** 2026-06-15  
**Author:** Polly (Reporting & Domain)  
**Status:** Implemented  

---

## Context

The live report on 2026-06-15 showed 0 listings in the main "All Current Roles — By Region"
section and 11 listings in the Review Queue. Every listing was flagged solely because the day
rate was missing. This is the norm for UK rail/construction contract postings — rates are
negotiated at offer stage — so the original gate was too restrictive.

---

## Decision

**Only geo-uncertainty flags route a listing to the Review Queue.**

| Flag | Source | New behaviour |
|------|--------|---------------|
| `country_unknown_assumed_non_uk` | Ada's `passes_uk` filter | **Review Queue** (geo quality; human must vet) |
| `day_rate_missing` (locally computed) | Reporter `get_sanity_reasons()` | **Soft note** in main section: "Rate: TBC — typical for UK contract market (negotiate at offer stage)" |
| `location_vague` / empty location (locally computed) | Reporter `get_sanity_reasons()` | **Soft note** in main section: "Location: {raw} — region not mapped" or "Location not stated — routed to Region TBC" |
| Any combination not including a geo flag | Any | **Main section with soft notes** |
| Any combination including a geo flag | Any | **Review Queue** (geo concern dominates) |

---

## Rationale

1. **UK contract market norm:** Most contract postings deliberately omit day rates. Publishers
   expect rates to be negotiated at offer stage. Treating `rate_missing` as a blocker removes
   100 % of legitimate market listings from Steve's view.

2. **Postcodes are valid locations:** "BT14LS" is Belfast (UK postcode). The geocoder failing to
   map a bare postcode to a named region is a tool limitation, not a data-quality failure.
   The listing is valid and should appear in the main section under "🔍 Region TBC".

3. **Geo-uncertainty is materially different:** `country_unknown_assumed_non_uk` means Ada's
   pipeline could not confirm the listing is UK-based. This IS a quality concern that warrants
   human review before surfacing to Steve.

---

## Implementation

Changed in `src/mechpm/reporter/`:

- **`grouping.py`**: `_GEO_REVIEW_FLAGS = {"country_unknown_assumed_non_uk"}`. New functions:
  `is_geo_flagged()` (Review Queue gate), `get_soft_notes()` (non-blocking display notes).
  `REGION_ORDER` extended with `"Region TBC"`. `resolve_region()` fallback → `"Region TBC"`.

- **`render.py`**: Partition uses `is_geo_flagged`. ⚠️ emoji added to `_flags_str` for any
  sanity-flagged listing. Pipeline card renders 💡 soft-note line. `_REGION_FLAGS` includes
  `"Region TBC": "🔍 Region TBC"`. Review Queue description updated.

- **`generate.py`**: `total_sanity_flagged` uses `is_geo_flagged` (reflects Review Queue count).

---

## Verification

- `pytest tests/` — 131 passed, 0 failed (baseline 128)
- Regenerated `reports/2026-06-15.md`: 11 listings in main section, 0 in Review Queue
- All 7 known-good listings (Advance TRS ×2, Jonathan Lee, Randstad, ARM ×3) visible in main section

---

## Future Considerations

- If `country_unknown_assumed_non_uk` listings are common for genuine UK roles (e.g., remote
  listings with no explicit location), revisit the `passes_uk` filter logic (Ada's domain).
- Consider adding Northern Ireland postcodes (BT*) to the region keyword map under a "Northern
  Ireland" bucket or "Other" sub-region in a future sprint.
- If other flag types are added by future pipeline components, evaluate each against this
  policy: geo uncertainty → Review Queue; data enrichment gap → soft note.
