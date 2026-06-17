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

## 2026-06-18: Turner & Townsend adapter shipped

New adapter: `src/mechpm/adapters/turner_townsend.py` (`TurnerTownsendAdapter`).

**Strategy:** POST JSON to `https://www.turnerandtownsend.com/api/careers/searchvacancies`
with `countries: ["United Kingdom"]`. T&T proxy sits in front of SmartRecruiters.
- Page size 50; pagination via `page` field; stop when `paginationModel.totalPages` reached
  or `max_pages_per_query` cap hit.
- Source listing ID extracted from numeric tail of `ref` URL
  (e.g. `…/postings/744000131433769` → `"744000131433769"`).
- Optional detail enrichment: GET each listing's SmartRecruiters `ref` URL for
  `typeOfEmployment.label` (→ contract_type_raw), `jobAd.sections.jobDescription.text`
  (→ description_raw), `postingUrl` (→ url). Controlled by `enrich_detail` config key
  (default false — enabled only if pipeline speed allows).
- Enrichment errors (timeout, 404, parse failure) are caught per-listing; original
  listing is preserved and a warning logged.
- All contract types returned; pipeline `passes_contract()` rejects permanent.
- employer always "Turner & Townsend"; agency None.
- Dedup by source_listing_id across all keyword/page fetches (same role returned
  by multiple keywords or across page boundaries is collapsed).

**Key pattern:** `_content_item_to_raw_listing()` + `_apply_detail()` are exposed as
module-level functions so tests can call them directly without network I/O.

**Test suite:** 74 new tests, all passing; 706 total passing (25 skipped).
2 pre-existing failures (rapidfuzz not installed in dev env) unrelated.
**Files changed:** `src/mechpm/adapters/turner_townsend.py`,
`tests/adapters/test_turner_townsend.py`, `config.toml`, `src/mechpm/cli.py`.
**Config key:** `[sources.turner_townsend]` — 7 keywords, max_pages=5, enrich_detail=false.
**Decision drop:** `.squad/decisions/inbox/michael-turner-townsend.md`

## 2026-06-17: Manpower Group adapter shipped

New adapter: `src/mechpm/adapters/manpower_group.py` (`ManpowerGroupAdapter`).

**Discovery (live recon 2026-06-17):**
- `careers.manpowergroup.co.uk/jobs?sort_type=relevance&query=...&radius=1600km` → 200 OK, full HTML
- Cloudflare CDN-only (not blocking); robots.txt allows `/jobs`
- Standard server-rendered HTML; 6 results/page; pagination via `&page=N` (1-indexed)
- No contract-type URL filter; no contract_type in search cards → `contract_type_raw = None`

**Strategy:** HTML scrape with `selectolax`. Card selector: `li.job-result-item`.
- Listing ID: numeric suffix of URL slug via `r'-(\d+)$'`
- Posted date: parsed from relative text ("Posted N days ago") → `_parse_posted_at()`
- Agency from `figure.recruiter-figure img[alt]`
- Pagination stops when `a[rel=next]` is absent

**Smoke test result:** 11 listings (2 queries returned results; 3 queries returned 0 cards).
**Full pipeline (5 keywords, 5 pages max):** 11 fetched, 0 stored — all were internal
Experis/ManpowerGroup recruiter roles, rejected by PM+mech filters. Source coverage is
limited; the adapter is working correctly.

**Test suite:** 59 new tests, all passing; 634 total passing (25 skipped).
**Files changed:** `src/mechpm/adapters/manpower_group.py`, `tests/adapters/test_manpower_group.py`, `tests/fixtures/adapters/manpower_group_page1.html`, `config.toml`, `src/mechpm/cli.py`.
**Decision drop:** `.squad/decisions/inbox/michael-manpower-group.md`

## Learnings

- T&T SmartRecruiters proxy: POST JSON to `/api/careers/searchvacancies`; MUST send
  `countries: ["United Kingdom"]` (not "gb" or "uk") — documented in coordinator recon.
- Listing ID lives in the numeric tail of the `ref` field (SmartRecruiters posting URL).
- Detail enrichment is opt-in: `enrich_detail = false` in config keeps run fast;
  set true to get contract_type_raw and full HTML description.
- Pattern for optional enrichment: expose `_apply_detail(listing, detail_json) → RawListing`
  as module-level function for direct test coverage without HTTP.
- `model_copy(update={...})` is the correct Pydantic v2 way to produce updated copies
  of immutable-ish BaseModel instances.
- Patch `asyncio.sleep` with `AsyncMock(return_value=None)` in fetch() tests to avoid
  real delays from inter-page sleeps.

