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

## 2026-06-17: Phenom platform adapter shipped (BAM Careers + Mace Group)

New adapter: `src/mechpm/adapters/phenom.py` (`PhenomAdapter`).

**Discovery (live recon 2026-06-17):**
- Both `www.bamcareers.com` and `careers.macegroup.com` run Phenom People ATS.
- Jobs embedded in HTML as JSON at `phApp.ddo.eagerLoadRefineSearch.data.jobs[]`
- `phApp.ddo.eagerLoadRefineSearch.totalHits` gives total count.
- Pagination: `?keywords={kw}&from=N&s=1` (10 per page, N=0,10,20,...).
- Job URL: `{base_url}/{site_path}/job/{jobSeqNo}`.
- No bot protection; permissive robots.txt (only /chatbot, /iauth, /socialAuth blocked).
- BAM embeds `companyName` per job; Mace does not — fallback to configured employer.
- Mace HTML contains placeholder `"Required Id"` entries — filtered by non-numeric reqId guard.

**Strategy:** Single `PhenomAdapter` class config-driven via `name`, `domain`, `site_path`, `employer_name`. Iterates `keywords_list`, paginates up to `max_pages_per_query=5`, union-deduplicates by `source_listing_id`.

**Smoke test results:**
- BAM Careers (2 keywords, 2 pages): 22 listings
- Mace Group (2 keywords, 2 pages): 29 listings

**Full pipeline results (4 keywords, 5 pages per keyword):**
- BAM Careers: 53 unique listings
- Mace Group: 77 unique listings
- Total 820 listings across 8 sources; 60 stored after filtering + dedup.

**Test suite:** 43 new tests, all passing; 526 total passing (25 skipped).
**Files changed:** `src/mechpm/adapters/phenom.py`, `tests/adapters/test_phenom.py`, `tests/fixtures/adapters/bam_careers_page1.html`, `tests/fixtures/adapters/mace_group_page1.html`, `config.toml`, `src/mechpm/cli.py`.
**Decision drop:** `.squad/decisions/inbox/michael-phenom-adapter.md`

