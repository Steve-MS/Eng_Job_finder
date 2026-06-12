# Orchestration Log — Michael The Engineer Jobs Adapter

**Session:** 2026-06-12T19:15 BST  
**Agent:** Michael (Backend / Scraping)  
**Task:** Implement The Engineer Jobs adapter (Phase A: httpx; Phase B: Playwright)  

## Outcome

**Status:** IMPLEMENTED  
**File:** `src/mechpm/adapters/the_engineer.py`  
**Class:** `TheEngineerAdapter(SourceAdapter)`  

## Deliverables

- ✓ Phase A: httpx with realistic Chrome 124 headers (User-Agent, Accept, Accept-Language, Referer, Sec-Fetch-*)
- ✓ Cloudflare challenge detection (403/503 status + CF markers, or 200 + CF markers)
- ✓ Phase B: Playwright async fallback (lazy import, raises ImportError if absent)
- ✓ Phase B import error handling (caught in fetch(), logs structured WARNING with fix command)
- ✓ Card selectors (article.job → data-job-id → .job-card → li.job → fallbacks)
- ✓ Per-field selectors (title, employer, location, salary, date)
- ✓ 5s crawl_delay between pages (both phases)
- ✓ Error resilience (exceptions caught, returns partial list or [])
- ✓ CLI wiring in `_get_registry()`
- ✓ Config block `[sources.the_engineer]` (enabled=false per directive)

## Verification Pending

- [ ] Phase A success on non-CF page (Arthur's gate)
- [ ] Phase A CloudflareChallengeError raised correctly
- [ ] Phase B Playwright fallback (requires pip install mechpm[browser] && playwright install chromium)

## Notes

Phase A is the default; returns [] on success (not a Phase B trigger).  
Phase B triggered only on CloudflareChallengeError or other unexpected exception.  
Playwright is optional dep ([browser] extra in pyproject.toml). Pipeline never crashes if absent.

