# Orchestration Log — Michael Aviation Job Search Adapter

**Session:** 2026-06-12T19:15 BST  
**Agent:** Michael (Backend / Scraping)  
**Task:** Implement Aviation Job Search adapter (HTML + aggregator metadata)  

## Outcome

**Status:** IMPLEMENTED  
**File:** `src/mechpm/adapters/aviation_job_search.py`  
**Class:** `AviationJobSearchAdapter(SourceAdapter)`  

## Deliverables

- ✓ Search URL + pagination (5 pages, 3s crawl_delay)
- ✓ Contract type filter (contract_types=2)
- ✓ Engineering category filter (job_categories=Engineering)
- ✓ Card selectors (data-test → data-testid → article/li/div variants)
- ✓ Per-field selectors (title, employer, location, salary, date)
- ✓ Aggregator metadata flag (metadata["aggregator"] = True)
- ✓ Error resilience (no selector match → WARNING logged, returns [])
- ✓ CLI wiring in `_get_registry()`
- ✓ Config block `[sources.aviation_job_search]` (enabled=false per directive)

## Verification Pending

- [ ] Live selector validation (Arthur's gate)
- [ ] Card count on page 1 (schema-change detection)

## Notes

Aggregator flag signals to Ada's dedup that source_listing_id (numeric job-ID) is the canonical dedup key, not fuzzy title+employer match. This prevents false positives from multiple employer/agency spellings of the same aggregated role.

No geo-filtering at adapter level; Ada's passes_uk gate is the correct gate.

