# Orchestration Log — Michael RailwayPeople Adapter

**Session:** 2026-06-12T19:15 BST  
**Agent:** Michael (Backend / Scraping)  
**Task:** Implement RailwayPeople adapter (__NEXT_DATA__ JSON parser)  

## Outcome

**Status:** IMPLEMENTED  
**File:** `src/mechpm/adapters/railwaypeople.py`  
**Class:** `RailwayPeopleAdapter(SourceAdapter)`  

## Deliverables

- ✓ Search URL + pagination (5 pages, 10s crawl_delay)
- ✓ __NEXT_DATA__ script extraction via selectolax HTMLParser
- ✓ Recursive JSON path discovery (pageProps → priority keys → full walk)
- ✓ Field mapping (Jobiqo JSON candidates per field)
- ✓ Dict-value normalisation (nested objects → name/label/text)
- ✓ Error resilience (HTTP/timeout/JSON/path failures logged, returns [])
- ✓ CLI wiring in `_get_registry()`
- ✓ Config block `[sources.railwaypeople]` (enabled=false per directive)

## Verification Pending

- [ ] __NEXT_DATA__ path discovery on live fetch (Arthur's gate)
- [ ] Jobs list location confirmation from production HTML

## Notes

Sector default: `rail` (Ada's SOURCE_DEFAULTS mapping handles this).  
No live test run yet — implementation against researched JSON structure.

