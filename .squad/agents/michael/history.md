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

## 2026-06-18: Advance TRS adapter shipped

New adapter: `src/mechpm/adapters/advance_trs.py` (`AdvanceTrsAdapter`).

**Strategy:** GET WP Job Manager REST API at `/wp-json/wp/v2/job-listings?job-types=7&per_page=100&page=N`.
- `job-types=7` filters to Contract roles server-side — no keyword search needed.
- Pagination driven by `x-wp-totalpages` response header (currently 2 pages / 102 jobs).
- Source listing ID: WP `id` field (integer, stored as string).
- `posted_at`: WP `date` field format `"2026-06-15T10:00:00"` treated as UTC.
- `salary_raw`: often empty — stored as `None` when blank/whitespace.
- `contract_type_raw`: set to `"Contract"` when `job-types` array contains 7.
- employer: `meta._company_name` with fallback to `_EMPLOYER` constant.
- Agency: always None (Advance TRS is the employer/recruiter).

**Config:** `[sources.advance_trs]` — no `keywords_list`; single flat fetch.
**Test suite:** 46 new tests, all passing; 809 total passing (25 skipped).
**Files changed:** `src/mechpm/adapters/advance_trs.py`, `tests/adapters/test_advance_trs.py`, `config.toml`, `src/mechpm/cli.py`.
**Decision drop:** `.squad/decisions/inbox/michael-advance-trs.md`

## 2026-06-18: Drupal Job Board adapter shipped (Building4Jobs, NCE Careers, CIC)

New adapter: `src/mechpm/adapters/drupal_jobboard.py` (`DrupalJobBoardAdapter`).

**Discovery (live recon 2026-06-18):**
- Task description said "all 3 sites run Drupal + Search API." Reality was more complex:
  - **Building4Jobs** runs Next.js/Jobiqo (NOT Drupal). Jobs in `__NEXT_DATA__` JSON.
  - **NCE Careers** and **Careers in Construction** run Drupal Epiq Jobs (server-rendered HTML).
  - NCE and CIC have identical HTML structure (same Drupal Epiq Jobs theme).
- Building4Jobs pagination: 1-indexed (page=0 == page=1; page=2 is second batch of 10).
- NCE/CIC pagination: 0-indexed (standard Drupal).

**Strategy:** Single `DrupalJobBoardAdapter` class dispatches to two parsers via `platform` config key:
- `platform = "jobiqo"` -> `parse_jobiqo_html()`: extract `__NEXT_DATA__` JSON from Next.js page.
- `platform = "drupal_epiq"` -> `parse_drupal_epiq_html()`: selectolax HTML parse.

**Field map — Jobiqo:** id -> source_listing_id; organization (str) -> employer;
address (list[str]) -> location_raw ([0]); salaryRange (list[Term]) -> salary_raw ([0]["label"]);
published (ISO 8601 with tz offset) -> posted_at.

**Field map — Drupal Epiq Jobs:** article[id="node-N"] -> source_listing_id;
h2.node__title a[title] -> title + href -> url; div.description span.date -> posted_at;
span.recruiter-company-profile-job-organization a -> employer; div.location span -> location_raw.

**Test suite:** 68 new tests, all passing; 877 total passing (25 skipped).
**Files changed:** `src/mechpm/adapters/drupal_jobboard.py`, `tests/adapters/test_drupal_jobboard.py`,
  `tests/fixtures/adapters/building4jobs_page1.html`, `tests/fixtures/adapters/nce_careers_page1.html`,
  `tests/fixtures/adapters/careers_in_construction_page1.html`, `config.toml`, `src/mechpm/cli.py`.
**Config keys:** `[sources.building4jobs]`, `[sources.nce_careers]`, `[sources.careers_in_construction]`.
**Decision drop:** `.squad/decisions/inbox/michael-drupal-jobboard.md`

## Learnings

- **Jobiqo/Next.js sites:** Jobs are in `__NEXT_DATA__` JSON under `props.pageProps.data.jobs.pages[]`.
  Field types differ from PowerShell display: `address` is `list[str]` (take [0]),
  `salaryRange` is `list[Term]` (take [0]["label"]), `published` is ISO 8601 with tz offset.
  Always verify field types in Python (json.loads()) not PowerShell (ConvertFrom-Json).
- **Jobiqo pagination** is 1-indexed: page=0 == page=1 (first batch); page=2 is second. Start at page=1.
- **Drupal Epiq Jobs pagination** is 0-indexed. Stop when no `div.views-row article` found.
- **Do NOT trust task discovery notes** as ground truth — always fetch live, inspect Python types.
- **Epiq Jobs date selector:** use `div.description span.date` (absolute), not `div.job__date` (relative).
- **Epiq Jobs title:** use `h2.node__title a.recruiter-job-link[title]` attr to avoid badge bleed-through.
- WP Job Manager REST API: GET `/wp-json/wp/v2/job-listings`; use `?job-types=<id>` to filter.
- Pagination via `x-wp-totalpages` response header (not in body); default to 1 if absent.
- WP date format is `"2026-06-15T10:00:00"` without timezone — treat as UTC.
- Always patch `mechpm.adapters.<module>.asyncio.sleep` (not bare `asyncio.sleep`).
- T&T: POST JSON to `/api/careers/searchvacancies`; send `countries: ["United Kingdom"]`.
- Detail enrichment is opt-in: `enrich_detail = false` keeps run fast.
- `model_copy(update={...})` is correct Pydantic v2 for updated copies.
