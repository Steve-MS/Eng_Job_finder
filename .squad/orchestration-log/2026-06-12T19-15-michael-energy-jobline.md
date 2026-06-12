# Orchestration Log — Michael Energy Jobline Adapter

**Session:** 2026-06-12T19:15 BST  
**Agent:** Michael (Backend / Scraping)  
**Task:** Implement Energy Jobline adapter (Drupal/Jobiqo HTML scrape)  

## Outcome

**Status:** IMPLEMENTED  
**File:** `src/mechpm/adapters/energy_jobline.py`  
**Class:** `EnergyJoblineAdapter(SourceAdapter)`  

## Deliverables

- ✓ Search URL + pagination (5 pages, 10s crawl_delay between pages)
- ✓ Card selectors (article.job-result → fallbacks to li.job-item → schema.org microdata)
- ✓ Per-field selector chains (title, employer, location, salary, date, snippet)
- ✓ schema.org markup prioritisation (itemprop elements most stable)
- ✓ source_listing_id extraction (data-job-id → numeric path segment → last path)
- ✓ No client-side date filtering (since applied by pipeline)
- ✓ Error resilience (all selectors empty → WARNING logged, returns [])
- ✓ CLI wiring in `_get_registry()`
- ✓ Config block `[sources.energy_jobline]` (enabled=false per directive)

## Verification Pending

- [ ] Live selector validation on actual Jobiqo theme (Arthur's gate)
- [ ] Card count on page 1 (schema-change detection)

## Notes

Robots.txt: /search/ disallowed; uses /jobs instead.  
Detail-fetch skipped (MVP); metadata["detail_fetched"] = False on all listings.
No UK-only filter at adapter level; Ada's passes_uk gate is the correct gate.

