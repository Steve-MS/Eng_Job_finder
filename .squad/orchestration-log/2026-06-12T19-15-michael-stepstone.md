# Orchestration Log — Michael Stepstone Adapter

**Session:** 2026-06-12T19:15 BST  
**Agent:** Michael (Backend / Scraping)  
**Task:** Implement Stepstone adapter (Totaljobs + CWJobs parameterised class)  

## Outcome

**Status:** IMPLEMENTED  
**File:** `src/mechpm/adapters/stepstone.py`  
**Class:** `StepStoneAdapter(SourceAdapter)`  

## Deliverables

- ✓ Adapter class with parameterised constructor (name, domain, search_path, crawl_delay)
- ✓ Query string + pagination (5-page cap, 3s crawl_delay)
- ✓ DOM selectors (data-at attributes, fallbacks)
- ✓ Relative date parser
- ✓ Error resilience (logs warning, returns [])
- ✓ CLI wiring in `_get_registry()` and `_build_adapters()`
- ✓ Config blocks for totaljobs + cwjobs (enabled=false per directive)

## Verification Pending

- [ ] Live DOM selector validation (Arthur's gate)
- [ ] No zero-card detection on page 1 (schema-change alert)

## Notes

No detail-fetch for MVP; snippet field from search cards used as description_raw. Metadata["detail_fetched"] = False on all listings.

