# Michael — History

## 2026-06-15: v0.2 sprint complete

v0.2 query-slate expansion sprint concluded. Multi-session continuation:
- Multi-query refactor (M1-M7) enabling all 7 sources
- Adzuna adapter (5 listings)
- Reed, RailwayPeople, Energy Jobline, Aviation integration
- 398 tests passing

**Final state: 44 stored across 5 sources, 18 with structured day-rate, 398 tests passing.**

## 2026-06-17: Michael Page adapter shipped

New adapter: `src/mechpm/adapters/michael_page.py` (`MichaelPageAdapter`).

**Strategy:** Drupal path-based HTML scrape at `/jobs/{keyword-slug}?page=N`.
- Confirmed working: `project-manager`, `project-engineer`, `project-director`, `contracts-manager`, `programme-manager`
- Non-existent slugs 404 gracefully (warn + skip)
- 30 results per page; up to 3 pages per keyword (configurable)
- 5 s crawl delay between requests
- All contract types returned; pipeline `passes_contract()` rejects permanent
- Agency always "Michael Page"; posted_at always None (not in search cards)
- Salary captured as raw string (day-rate "£320 - £375 per day" or annual)

**Smoke test result:** 78 listings (25 Interim/Temporary, 8 with day-rate salary).
**Test suite:** 49 new tests; 575 total passing (25 skipped).
**Files changed:** `src/mechpm/adapters/michael_page.py`, `tests/adapters/test_michael_page.py`, `tests/fixtures/adapters/michael_page_page1.html`, `config.toml`, `src/mechpm/cli.py`.
**Decision drop:** `.squad/decisions/inbox/michael-michael-page-adapter.md`

