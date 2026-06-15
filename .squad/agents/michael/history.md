# Michael — History

## Project Seed (2026-06-12)
- **Project:** Mech-PM-Finder
- **Owner:** Steve
- **My role:** Backend / Scraping. I own source adapters, scheduling, and raw-listing persistence.
- **Mission:** Regularly scan multiple UK job boards for contract PM roles in mechanical engineering.
- **Tech stack:** TBD — wait for Tommy's decision.

## Learnings

### 2026-06-12: Source shortlist research

**Tier-1 picks and key fetch facts:**

| Source | Strategy | Key fact |
|---|---|---|
| Reed.co.uk | **Official JSON API** — free key via reed.co.uk/developers | Basic-auth (API key as username). `GET /api/1.0/search?keywords=project+manager+mechanical&locationName=UK&contract=true&resultsToTake=100`. Returns `salaryType` (per day / per hour / per annum) — critical for day-rate detection. Paginate with `resultsToSkip`. |
| Totaljobs | **HTML scrape** — robots.txt explicitly allows `/jobs/*?q=` | StepStone platform. URL: `https://www.totaljobs.com/jobs/project-manager/engineering-jobs?contract=true`. Also allows `/jobs/` path. `/JobSearch/RSS.aspx` is *disallowed* — no RSS. |
| CWJobs | **HTML scrape** — same StepStone platform as Totaljobs | robots.txt mirrors Totaljobs; `/jobs/*?q=` allowed. URL: `https://www.cwjobs.co.uk/jobs/project-manager/in-uk?contract=true`. Share adapter code with Totaljobs. |

**Hard decisions made:**
- **LinkedIn** — ToS robots.txt header explicitly says "use of automated means is strictly prohibited". Will NOT implement.
- **Indeed UK** — RSS format deprecated (404s confirmed in probe). Strong anti-bot (Cloudflare). robots.txt restricts. Deprioritized.
- **Hays.co.uk** — `Disallow: /jobs-search/` in robots.txt. Will not scrape.
- **IMechE** — `www.imeche.org` robots blocks all crawlers; `careers.imeche.org` resolves (200) and has no restrictive robots.txt — viable Tier-2 target.
- **Jobserve** — `/Job-Search.aspx` disallowed but modern `/gb/en/Job-Search/` returns 200. JS-heavy page likely needs Playwright. Tier-2.

**Reed API note:** robots.txt has `Disallow: /api/` for `*`, but this is the internal browser API path. The Developer API is an explicitly published, key-gated service — authorized use by registered API key holders is the intended use case; robots.txt directive is aimed at unauthorized scrapers, not registered API consumers.

### 2026-06-12: Vertical-specialist source addendum

**Tier-1 picks from vertical research (addendum to generalist shortlist):**

| Source | Strategy | Key fact |
|---|---|---|
| **RailwayPeople.com** | **HTML GET → parse `__NEXT_DATA__` JSON blob** — Next.js SSR (Jobiqo platform). URL: `https://www.railwaypeople.com/jobs?keywords=project+manager&jobtype=contract`. robots.txt: `Allow: /`, `Crawl-delay: 10`. | Meta description in HTML confirmed "308 Jobs" for PM+contract. No Playwright needed — listings embedded in `window.__NEXT_DATA__`. Enforce 10 s delay. |
| **Energy Jobline** | **HTML scrape** — Jobiqo/Drupal platform. URL: `https://www.energyjobline.com/jobs?keywords=project+manager+mechanical&location=United+Kingdom&contract_type=contract`. robots.txt: `Crawl-delay: 10`, disallows `/search/` (legacy path) and admin paths, but `/jobs?...` confirmed working. | Jobiqo platform — same as RailwayPeople. Possible shared adapter (different HTML structure from Next.js variant, but same query param conventions). Energy + O&G + renewables + nuclear all on one board. |
| **The Engineer Jobs** | **HTML scrape** — Cloudflare-managed robots.txt; `User-agent: *` `Allow: /`. URL: `https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract`. Confirmed search returns results. | Cloudflare blocks named AI bots (GPTBot etc.) but explicitly allows `User-agent: *`. Use polite User-Agent string. Mark Allen Group platform. |
| **Aviation Job Search** | **HTML scrape** — robots.txt allows all except `/api/*` and action paths. URL: `https://www.aviationjobsearch.com/en-GB/jobs?title=project+manager&job_categories=Engineering`. | Contract filter param: `contract_types=2`. Standard HTML, no JS rendering required. Confirm param values via manual probe before build. |

### 2026-06-12: Vertical-specialist source addendum

**Tier-1 picks from vertical research (addendum to generalist shortlist):**

| Source | Strategy | Key fact |
|---|---|---|
| **RailwayPeople.com** | **HTML GET → parse `__NEXT_DATA__` JSON blob** — Next.js SSR (Jobiqo platform). URL: `https://www.railwaypeople.com/jobs?keywords=project+manager&jobtype=contract`. robots.txt: `Allow: /`, `Crawl-delay: 10`. | Meta description in HTML confirmed "308 Jobs" for PM+contract. No Playwright needed — listings embedded in `window.__NEXT_DATA__`. Enforce 10 s delay. |
| **Energy Jobline** | **HTML scrape** — Jobiqo/Drupal platform. URL: `https://www.energyjobline.com/jobs?keywords=project+manager+mechanical&location=United+Kingdom&contract_type=contract`. robots.txt: `Crawl-delay: 10`, disallows `/search/` (legacy path) and admin paths, but `/jobs?...` confirmed working. | Jobiqo platform — same as RailwayPeople. Possible shared adapter (different HTML structure from Next.js variant, but same query param conventions). Energy + O&G + renewables + nuclear all on one board. |
| **The Engineer Jobs** | **HTML scrape** — Cloudflare-managed robots.txt; `User-agent: *` `Allow: /`. URL: `https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract`. Confirmed search returns results. | Cloudflare blocks named AI bots (GPTBot etc.) but explicitly allows `User-agent: *`. Use polite User-Agent string. Mark Allen Group platform. |
| **Aviation Job Search** | **HTML scrape** — robots.txt allows all except `/api/*` and action paths. URL: `https://www.aviationjobsearch.com/en-GB/jobs?title=project+manager&job_categories=Engineering`. | Contract filter param: `contract_types=2`. Standard HTML, no JS rendering required. Confirm param values via manual probe before build. |

**Sectors with no specialist board recommended for Tier 1:**
- **Automotive** — PM contracts cluster on generalist boards + agencies. JustAutomotive exists but volume too low for MVP.
- **Maritime** — Very low UK mech-eng PM contract volume. Skip for MVP.
- **Construction/Civil specialist** — NCE Jobs, ICE Careers, IMechE Careers all low-volume. Covered sufficiently by Careerstructure (Tier 2) + generalist boards.

**Fetch gotchas by sector:**
- **Rail (RailwayPeople):** Next.js pages — page source includes `<script id="__NEXT_DATA__">` JSON blob. Parse this directly; much cleaner than CSS-selector scraping rendered HTML. Respect `Crawl-delay: 10`.
- **Energy (Energy Jobline):** robots.txt disallows `/search/` (the old Drupal search) but `/jobs?keywords=...` is the correct modern path and is NOT disallowed. Don't confuse the two.
- **Aerospace (Aviation Job Search):** `/api/*` is disallowed — do NOT try to call their internal API. Use the public search HTML page only.

---

## 2026-06-12: Implementation Sprint 1 Complete — Cross-Team Sync
**Sprint outcome:** Tommy (architecture), Michael (scaffold + Reed), Ada (extraction + storage), Polly (reporter), Arthur (tests) all delivered. Architecture finalisation is binding. Full project scaffold + 28-field schema + 3-tier extraction + dedup + SQLite + Markdown reporter + 84-test suite complete. Orchestration logs: `.squad/orchestration-log/2026-06-12T{17:30,18:30}Z-{agent}.md`. Session log: `.squad/log/2026-06-12T1830-implementation-sprint-1.md`. **⚠️ Arthur surfaced 2 real defects for Ada's immediate attention:** (1) UAE/Dubai location filter rejects valid expat-PM listings (medium, ~2% impact), (2) "civil engineering" keyword false-fires mech-domain filter (high, ~8% false positives, affects dedup precision). All acceptance criteria gates documented. Design locked; implementation ready to proceed to Sprint 2 (Michael's 6 remaining adapters).
- **Construction (Careerstructure):** StepStone platform (adapter reuse from Totaljobs/CWJobs). Complex robots.txt but no blanket `Disallow: /` for `User-agent: *`. The `Disallow: /*&page=*` rule blocks `&page=N` pagination — use `?page=N` (leading `?`) if pagination is needed, or reverse-sort and stop on seen IDs.
- **Cross-sector (The Engineer Jobs):** Cloudflare on robots.txt delivery. Assume same Cloudflare fronting on job pages — standard browser-like headers required. Confirm 200 response in adapter spike before committing.
- **Jobiqo platform (RailwayPeople + Energy Jobline):** Confirm whether both use identical `__NEXT_DATA__` structure. Energy Jobline appeared Drupal-based in probe — may be an older Jobiqo theme without Next.js. Treat as separate adapters until confirmed.

**Updated unified Tier-1 (MVP, 7 sources):**
Reed, Totaljobs, CWJobs, RailwayPeople, Energy Jobline, The Engineer Jobs, Aviation Job Search

**DefenceJobs.co.uk:** `User-agent: * Disallow: /` — fully hostile. Do not implement.

---

## 2026-06-12: Sprint #1 + #2 — Scaffold + Reed Adapter Shipped

### Reed query string (baked in, tunable via config.toml)
```
keywords = "project manager mechanical engineering"
locationName = "UK"
contract = true
resultsToTake = 100
resultsToSkip = 0, 100, 200, …  (pagination)
```
Rationale: broad enough to catch all mechanical-discipline PM contracts (nuclear, rail, oil & gas, aerospace) via the single generalist term. `contract=true` is a Reed filter enum, not a freetext param. Can be narrowed in config.toml without code changes.

### Reed pagination + throttle behaviour
- Reed returns a `results` JSON array. Empty array = no more pages.
- When `len(results) < resultsToTake`, that is the final page — no extra request needed.
- Safety cap of 500 listings/run avoids unbounded runs if query is too broad.
- Rate limit: 10 req/min (free tier) → 6 s sleep (`_PAGE_DELAY_SECONDS`) between successive page requests *inside* `fetch()`. The orchestrator `crawl_delay` for Reed is 0 (no extra inter-source delay required).
- The Reed API does **not** expose a `postedAfter` query parameter. `since` filtering is applied client-side on `posted_at` after each job is mapped. Future work: confirm whether `distanceFromLocation` or date fields exist in advanced API.

### Reed date formats observed
Reed's `date` field returns `"DD/MM/YYYY"` in standard responses. The `_parse_reed_date()` helper tries three formats (`%d/%m/%Y`, `%Y-%m-%dT%H:%M:%S`, `%Y-%m-%d`) as a defensive measure for schema drift.

### Package layout decisions baked in
- **tomllib** (stdlib, Python 3.11+) used instead of `tomli` third-party package — no extra dependency since we require Python ≥3.12.
- **argparse** (stdlib) used instead of Click — keeps deps minimal.
- **hatchling** chosen as build backend (modern, zero-config for `src/` layout).
- **selectolax** declared as HTML-parsing dep for future HTML adapters; much faster than BeautifulSoup for large listing pages.
- `config.toml` uses `[sources.<name>]` flat tables (not `[[sources]]` array-of-tables) for named-source lookup without iteration.
- Adapter `crawl_delay` is inter-*source* delay managed by the orchestrator. Intra-source page delays are the adapter's own responsibility (Reed: 6 s; HTML adapters: per robots.txt).
- `RawListing.employer` carries the Reed `employerName` (which may be an agency name when a recruiter posts). `agency` is `None` for Reed; future HTML adapters can populate it when clearly labelled.
- `metadata` dict carries Reed-specific extras: `currency`, `salary_type` (per day / per annum / per hour — critical for day-rate detection by Ada's extractor), `applications`, `expiration_date`.

---

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: implement 7-source adapters per Tommy's contract. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.

---

## Learnings

### 2026-06-14: Pipeline Wiring Sprint — Tommy's Spec Implemented

**What I built:**

| Module | What changed |
|---|---|
| `src/mechpm/pipeline.py` | New wiring module — `process_and_report()` + `PipelineResult` dataclass |
| `src/mechpm/orchestrator.py` | Now emits `data/raw/{date}/run_manifest.json` after each run |
| `src/mechpm/storage/sqlite.py` | Added `first_seen_at` + `times_seen` columns; idempotent migration; new `upsert_normalized()` with `ON CONFLICT DO UPDATE`; `get_listings_since()` query |
| `src/mechpm/reporter/generate.py` | New entry point `generate_report()` wrapping `render_weekly()` — reads manifest, marks 🆕 listings |
| `src/mechpm/reporter/__init__.py` | Exports `generate_report` |
| `src/mechpm/cli.py` | `run-all` now wires full pipeline; `--skip-fetch`, `--since`, `--skip-report` flags added |
| `tests/test_pipeline_e2e.py` | 7 integration tests: basic run, idempotency, quarantine |
| `.gitignore` | Added `data/quarantine/` |

**Live run counts (--skip-fetch on real railwaypeople.jsonl, 50 listings, 2026-06-14):**

| Stage | Count |
|---|---|
| fetched | 50 |
| extracted | 50 |
| quarantined | 0 |
| filtered_out | 47 |
| deduped | 0 (rapidfuzz not installed — identity fallback) |
| stored | 3 |
| reported | True |

High filter-out rate (47/50) is expected: the railwaypeople smoke run returned rail-generic listings (points operators, commercial managers, etc.), not mech-PM contracts. The 3 that passed were genuine PM/mechanical matches.

**Test counts:** 88 → 95 passed (7 new e2e tests added, 0 previously passing tests broken).

**Surprises / gotchas:**

1. **Extractor interface** — `extract(raw)` is clean and predictable. Ada's extractor is robust; no ValidationErrors fired on the real JSONL.
2. **Reporter entry point** — `render_weekly()` accepts `RunMetadata` + `listings` and is completely self-contained. I added `generate_report()` as the new entry point in `reporter/generate.py` rather than touching render logic. This respects the "do not modify Polly's render" constraint cleanly.
3. **`INSERT OR REPLACE` vs `ON CONFLICT DO UPDATE`** — The existing `insert_normalized()` uses SQLite's `INSERT OR REPLACE` which deletes + reinserts on conflict, resetting `first_seen_at`. The new `upsert_normalized()` uses proper `ON CONFLICT(listing_id) DO UPDATE` to preserve `first_seen_at` while incrementing `times_seen`. Both coexist; pipeline uses `upsert_normalized()`.
4. **`rapidfuzz` not in venv** — dedup falls back to identity (no-op). Noted for Scribe — should be added to pyproject.toml if not already there.
5. **run.bat** — Already calls `python -m mechpm.cli run-all %*` (pass-through args); no change needed.

---

## 2026-06-14: RailwayPeople Adapter Full-Field Calibration

**Problem:** Adapter fetched 50 listings but only `title` was populated. All other fields (employer, location, source_url, posted_at) were null.

**Root cause:** The Jobiqo `__NEXT_DATA__` schema uses non-standard field names not covered by the original mapping code.

### Confirmed JSON field map (Jobiqo platform, 2026-06-14)

JSON path from page root: `props.pageProps.data.jobs.pages` (list of job dicts)

| RawListing field | JSON key | Notes |
|---|---|---|
| `title` | `title` | flat string ✓ (already worked) |
| `source_listing_id` | `id` | integer — coerce to str |
| `employer` | `organization` | flat string (NOT `company` / `employer`) |
| `location_raw` | `address` | **list** of "City, Country" strings — join with `"; "` |
| `url` | `urlNoPrefix` | flat relative path `/job/slug-id` — prepend base URL |
| (fallback) | `url.path` | nested dict `{"__typename":"Url","path":"/job/..."}` |
| `posted_at` | `published` | ISO-8601 with TZ offset e.g. `"2026-06-03T14:25:56+01:00"` |
| `salary_raw` | `salaryRangeFree` | nested dict with `minSalary`/`maxSalary`/`currencyCode`/`salaryUnit`; all null in current search results |
| `contract_type_raw` | (none) | default `"Contract"` since search uses `jobtype=contract` |
| `description_raw` | (none) | not in search listing JSON; only on detail page — left `None` |

### Field-name surprises
- `organization` (not `company`/`employer`) is the flat employer string.
- `address` is a **list**, not a string. Multi-location jobs can have 5+ entries.
- `url` is a nested dict `{"__typename": "Url", "path": "/job/slug"}` — NOT a string. `urlNoPrefix` is the same value as a flat string and is easier to use.
- `salaryRangeFree` is always `{minSalary: null, maxSalary: null, ...}` in search results — rates not publicly disclosed by RailwayPeople listings.
- `_find_jobs_list()` discovery already found the correct path because the job dicts have a `"url"` key (even nested), satisfying the `_URL_FIELDS` intersection check.

### Live counts (2026-06-14)
- 50 fetched / 50 extracted / 0 quarantined / 47 filtered_out / 3 stored
- Population rates: title 100%, employer 100%, location 100%, url 100%, posted_at 100%
- Rate fields: 0% (expected — search results never expose salary)
- Test suite: 106 passed, 25 skipped, 0 failed (was 95 before this sprint)

---

## 2026-06-15: Reed locationName Fix — Regression Test + Live Verification

**Problem identified:** Reed adapter was sending `locationName=UK` to the Reed API. Reed treats `locationName` as a city/town field and does not recognize "UK" as a valid location code → returns **0 results** every time.

**API behaviour confirmed via direct testing:**
- With `locationName=UK`: 0 results returned
- Without `locationName` param: 29 contract mechanical-PM listings returned
- Reed is UK-only by default — no location param needed for nationwide search

**Fix applied (2026-06-15):**
1. Changed `_DEFAULT_LOCATION = "UK"` to `_DEFAULT_LOCATION = ""` in `src/mechpm/adapters/reed.py:26`
2. Modified `_fetch_page()` params dict construction (lines 146-153) to only include `locationName` when `self.location` is non-empty
3. Updated `config.toml:9` to set `location = ""` (empty string)
4. Added regression test suite: 5 new test cases in `tests/adapters/test_reed.py` covering:
   - Empty location omits `locationName` param
   - Non-empty location includes `locationName` param
   - Default location is `""`
   - Custom locations preserved
   - Keywords and contract params always present

**Live verification (2026-06-15T09:05:13Z):**
```
Reed: fetched 29 listing(s) (pages_fetched=1, since=None).
HTTP Request: GET https://www.reed.co.uk/api/1.0/search?keywords=project+manager+mechanical+engineering&contract=true&resultsToTake=100&resultsToSkip=0
(note: locationName parameter is NOT present)
```

**Test results:** 128 passed (baseline 123 + 5 new Reed tests), 25 skipped, 0 failed

---

## 2026-06-15: JSONL Overwrite Policy + rapidfuzz Promoted to Core Dep

**Task:** Implemented Tommy's spec `tommy-jsonl-policy-and-deps.md`.

### Changes made

| File | Change |
|---|---|
| `src/mechpm/orchestrator.py` | `_persist_jsonl` now opens in `"w"` mode (was `"a"`); docstring updated |
| `pyproject.toml` | `rapidfuzz>=3.0` added to `[project.dependencies]` (was absent — fallback warning fired every run) |
| `tests/test_orchestrator.py` | New file: regression test for overwrite + smoke tests for rapidfuzz |
| `data/raw/2026-06-14/railwaypeople.jsonl` | Deleted (inflated from 3× calibration runs) |
| `data/raw/2026-06-15/railwaypeople.jsonl` | Deleted (inflated), recreated fresh by verification runs |

### Key learning: rapidfuzz dedup catches ghost duplicates that identity dedup misses

The identity (content-hash) fallback misses ghost duplicates where the same listing appears with minor field variations across re-runs — e.g., slightly different `fetched_at` timestamps or whitespace. The Jaro-Winkler title similarity in rapidfuzz's dedup pipeline correctly identifies these as duplicates. This was confirmed during today's verification: with rapidfuzz installed, `deduped: 6` was reported (vs `deduped: 0` under identity fallback). Those 6 ghost duplicates would have inflated the report if rapidfuzz had not been available.

### Live verification (2026-06-15)

- **Two successive runs of `run-all --source railwaypeople`:** File size after 2nd run = 50,391 bytes (= exactly one run's output). Old appended file was 100,782 bytes (= 2× that). Overwrite confirmed working.
- **rapidfuzz version installed:** 3.14.5
- **Test count:** 131 passed, 25 skipped, 0 failed (baseline was 128; +3 new tests)
- **No "rapidfuzz not installed" WARNING** in any pipeline run after this change.

---

## 2026-06-15: The Engineer Jobs — Cloudflare Hard Block Confirmed

**Task:** Calibrate the `the_engineer` adapter and determine if Cloudflare blocks it.

**Site status:** **BLOCKED** — Cloudflare hard block. Adapter disabled.

### Probe result (2026-06-15T09:45Z)

```
GET https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131.0.0.0 Safari/537.36

HTTP 403
Server: cloudflare
CF-RAY: a0c053ada97c2968-LHR
Body: "Attention Required! | Cloudflare" / "Sorry, you have been blocked"
```

### Anti-bot detection result

**Hard Cloudflare block** — not a JS challenge (which would return 200 + challenge page). The Cloudflare edge node returned a 403 before the request reached the origin server. This means:

- Phase A (httpx + browser headers) fails at the edge — browser User-Agent alone is insufficient.
- Phase B (Playwright headless) would likely also fail: headless Chromium is detectable by Cloudflare's bot score fingerprinting (JS entropy, canvas, WebGL, missing browser APIs). Attempting stealth-patching would violate charter.

### Fetch strategy

Not applicable — calibration stopped at Cloudflare detection gate per charter rules.

### Actions taken

| Action | Detail |
|---|---|
| `config.toml` | `enabled = false` for `[sources.the_engineer]` with dated comment |
| Raw fixture saved | `tests/fixtures/adapters/the_engineer_recon.html` (5,970 bytes — full Cloudflare error page) |
| Decision drop written | `.squad/decisions/inbox/michael-engineer-blocked.md` — documents evidence, alternatives, recommended next step (RSS probe) |
| Skill created | `.squad/skills/cloudflare-detection/SKILL.md` — reusable Cloudflare detection protocol for future adapter calibrations |

### Recommended next step (for Tommy)

Probe RSS endpoints: `https://jobs.theengineer.co.uk/rss`, `/feed`, `/rss.xml`. RSS feeds typically bypass Cloudflare bot protection entirely. If a feed exists, Michael can build an RSS adapter in one sprint.

### Impact

- **-1 source** temporarily. Pipeline runs on 6 sources.
- No regressions — existing tests unaffected (disabled source skipped by orchestrator).
- Test count unchanged: **138 passed, 25 skipped** at time of commit.

---

## 2026-06-15: Energy Jobline Adapter Calibration

**Task:** Calibrate `energy_jobline.py` against the live site and add fixture regression test.

### Site structure (confirmed 2026-06-15)

| Field | CSS / regex | Notes |
|---|---|---|
| Card | `article.node--job-per-template` | Drupal node; class includes `node-job` variants |
| ID | regex `node-(\d+)` on article `id` attr | Drupal NID, always numeric |
| Title | `h2.node__title a` text | Absolute `href` also gives URL |
| URL | `h2.node__title a` `href` | Always absolute — no `urljoin` needed (kept as safe fallback) |
| Employer | `.recruiter-company-profile-job-organization a` | Inside `.description` div |
| Location | `.location span` | Optional — absent on ~40% of search cards |
| Date | `.date` text → strip trailing `,` | **US format MM/DD/YYYY** (`06/15/2026`). Old adapter had `%d/%m/%Y` which silently failed. |
| Salary | *(not in search cards)* | Always `None` for search results |
| Contract type | hardcoded `"contract"` | Baked into search URL `&contract_type=contract` |

### Key bug fixed: date format was wrong

Old adapter used `%d/%m/%Y` — this fails for US-formatted dates like `06/15/2026` (month 15 = ValueError → `posted_at = None` for every listing). Fixed by adding `%m/%d/%Y` as primary slash-format before the legacy `%d/%m/%Y`.

### Key bug fixed: pagination was off-by-one

Old adapter built `&page=2` for the second page. EJL uses 0-based pagination: second page → `&page=1`, third → `&page=2`. Fixed to `&page={page_num - 1}`.

### Anti-bot: none

Plain HTTP with browser User-Agent headers returns HTTP 200. No Cloudflare, no JS rendering required. `Crawl-delay: 10` respected between page fetches.

### `_parse_html()` extracted for testability

Moved parse logic out of `_fetch_page()` into a standalone `_parse_html(html, page_url)` function. Tests call it directly without making network requests.

### Live results (2026-06-15T09:59Z)

- 5 pages fetched (pages 1–5), 20 listings/page = **100 listings total**
- All 5 HTTP requests returned 200 OK
- Field population: title 100%, id 100%, employer 100%, url 100%, posted_at 100%, location_raw ~60%
- Pagination URL sequence confirmed correct:
  ```
  page 1: /jobs?keywords=...&contract_type=contract
  page 2: /jobs?keywords=...&contract_type=contract&page=1
  page 3: /jobs?keywords=...&contract_type=contract&page=2
  ...
  ```

### Tests (2026-06-15)

- **15 new tests** in `tests/adapters/test_energy_jobline.py`
- Full suite: **153 passed, 25 skipped, 0 failed** (baseline 138 + 15 new)

### Note on result relevance

Search `keywords=project+manager+mechanical&contract_type=contract` returns broadly-matched results — today's page 1 was dominated by Iberdrola Renewables engineering roles (not PM titles). Ada's domain + title filters will handle relevance. The 6 listings that passed the full pipeline filter were genuine matches. Volume is sufficient (100 raw/run); precision is Ada's concern, not mine.

---

## 2026-06-15: Aviation Job Search Adapter Calibration

**Task:** Calibrate `aviation_job_search.py` — live smoke returned HTTP 200 but 0/13 selectors matched.

### Root cause: fully client-side rendered application

The original adapter assumed listings appear in static HTML (CSS-selector strategy). Live recon confirmed this is entirely wrong:

- Search results page (`/en-GB/jobs?...`) embeds `AppData.is_ssr = false` — all listings are loaded via an internal AJAX call to `/api/v1/jobs`
- The `#searchResults` div is empty (`d-none`) in the static HTML response
- **`/api/v1/jobs` is explicitly `Disallow`-ed in robots.txt** — cannot call it directly

### Strategy chosen: Sitemap + schema.org/JobPosting LD+JSON

| Step | Detail |
|---|---|
| 1 | Fetch `https://www.aviationjobsearch.com/en-GB/sitemap/jobs.xml` (publicly listed in robots.txt) |
| 2 | Filter URLs by `/management/` path OR PM-keyword in title slug (`project-manag`, `programme-manag`, `project-lead`, `project-coord`) |
| 3 | Fetch each matched job-detail page (SSR — confirmed 200 in static HTTP) |
| 4 | Parse `schema.org/JobPosting` LD+JSON block from each page |
| 5 | Map to `RawListing` |

### Why this works

Individual job-detail pages (`/jobs/{cat}/{sub}/{title-id}`) ARE server-side rendered. Each contains a `<script type="application/ld+json">` block with a `{"@type": "JobPosting"}` dict containing all required fields.

### Confirmed LD+JSON field map (2026-06-15)

| RawListing field | JSON key | Notes |
|---|---|---|
| `title` | `title` | flat string |
| `source_listing_id` | trailing `-NNN` in URL slug | e.g. `engineering-project-manager-by-jmc-...-607645` → `607645` |
| `employer` | `hiringOrganization.name` | always populated |
| `location_raw` | `jobLocation.address.{locality, region, country}` | joined with `", "` |
| `posted_at` | `datePosted` | ISO date `"YYYY-MM-DD"` |
| `contract_type_raw` | `employmentType` | `"FULL_TIME"`, `"CONTRACTOR"`, etc. |
| `description_raw` | `description` | full HTML description string |
| `salary_raw` | `baseSalary` (rarely set) | schema.org MonetaryAmount — almost never present on this board |
| `metadata["valid_through"]` | `validThrough` | expiry date |
| `metadata["org_id"]` | `identifier.value` | employer's org ID (not job ID) |
| `metadata["aggregator"]` | hardcoded `True` | board aggregates employer ATS feeds |

### Critical bug found: Brotli encoding not handled by httpx

When `Accept-Encoding: gzip, deflate, br` is sent (it was in `_BROWSER_HEADERS`), the server prefers **Brotli** (`Content-Encoding: br`). httpx does not have a built-in Brotli decoder. The sitemap returned 7,453 garbled bytes instead of 60,671 readable XML bytes. `_SITEMAP_RE.finditer()` found 0 matches → 0 URLs → 0 listings.

**Fix:** Removed `br` from `Accept-Encoding`; now uses `"gzip, deflate"` only. httpx auto-decompresses both.

**General rule:** Never include `br` in `Accept-Encoding` when using httpx without the optional `brotli` PyPI package installed.

### Live results (2026-06-15T10:09Z)

- Sitemap: 297 total jobs, **6 matched** (all `/management/` category)
- All 6 HTTP requests returned 200 OK; LD+JSON parsed on all 6
- 6 listings returned by `fetch()`; **6 stored** in pipeline run
- Field population: title 100%, employer 100%, location_raw 100%, url 100%, posted_at 100%

### Volume note

This is a specialist aviation board. 297 total active listings; 6 management roles is the correct universe today. Volume will fluctuate with the sitemap update cycle (daily). Ada's filters will handle relevance; low volume here is expected and appropriate.

### Tests (2026-06-15)

- **12 new tests** in `tests/adapters/test_aviation_job_search.py`
- 2 additional smoke tests now pass (`test_adapter_module_importable`, `test_adapter_has_required_interface`)
- Full suite: **181 passed, 25 skipped, 0 failed** (baseline before this sprint: 153)

---

## 2026-06-15: Adzuna API Adapter

**Task:** Build `src/mechpm/adapters/adzuna.py` — ToS-clean partner API route to Totaljobs, CWJobs, Indeed and other Akamai-blocked UK boards.

### API quirks and field map

| RawListing field | Adzuna JSON key | Notes |
|---|---|---|
| `source_listing_id` | `id` | string in JSON response |
| `title` | `title` | flat string |
| `employer` | `company.display_name` | nested dict; safe-get required |
| `location_raw` | `location.display_name` | nested dict; safe-get required |
| `url` | `redirect_url` | Adzuna tracking URL; contains `utm_source={app_id}` — **must be scrubbed in fixtures** |
| `posted_at` | `created` | ISO-8601 UTC `"2026-06-09T19:56:23Z"` — clean format, no parsing surprises |
| `salary_raw` | `salary_min` / `salary_max` | see salary semantics below |
| `contract_type_raw` | `contract_type` | `"contract"` confirmed for all results under `contract=1` filter |
| `description_raw` | `description` | truncated snippet (~400 chars) — full description at `redirect_url` |

### Salary semantics (⚠ critical for Ada)

- `salary_min` / `salary_max` are **annualised salary equivalents**, NOT day rates. Adzuna's indexer converts day-rate postings to annual figures using a standard multiplier before storage. Ada MUST apply its own day-rate heuristics and not treat these as day rates.
- `salary_is_predicted == "1"` means Adzuna's ML model estimated the salary; `"0"` means extracted verbatim from the posting. The `(predicted)` suffix in `salary_raw` flags this for Ada.
- `metadata["salary_annualised"] = True` is set on every Adzuna listing as an explicit signal.
- A small fraction of listings (e.g., Mane Contract Services in today's fixture) have `salary_is_predicted = "0"` with high annual equivalents (£117k–£130k) — these appear to be salary-for-permanent-equiv postings mismatched to the contract query. Ada's rate extraction will need to recognise these as implausibly high day rates and flag them.

### Contract filter behaviour

`contract=1` in the query params reliably restricts results to `contract_type == "contract"`. All 5 fixture results and all 10 live results confirmed. The filter is applied at source level by Adzuna — no client-side filtering needed for `contract_type_raw`.

### `__CLASS__` fields

Adzuna's API returns `__CLASS__` fields in nested objects (e.g., `"__CLASS__": "Adzuna::API::Response::Company"`). These are internal Adzuna typing annotations. Ignore them; do not parse or store them.

### Rate limits and pagination

- Free tier: 50 req/min, 1 000 req/day
- At results_per_page=50 and the 10-page hard cap: ≤10 requests/run — comfortably within daily quota
- Today's live run (10 total results, 1 page fetched): confirms the narrow current query returns only 10 results total. Tommy's v0.2 broader query slate will increase this.
- Live page 1 returned 5 results (fixture, results_per_page=5) and 10 results (live run, results_per_page=50). Total count was 10 in both cases — confirms only 1 page of results with the current narrow query.

### `redirect_url` security note

Every Adzuna `redirect_url` contains `utm_source={app_id}` in the query string. When saving fixtures, the scrub step MUST replace the literal app_id value in the URL. Verified: PowerShell regex scrub correctly replaced `app_id` with `REDACTED_APP_ID` in all 5 fixture URLs.

### Patterns that differ from Reed

| Aspect | Reed | Adzuna |
|---|---|---|
| Auth | HTTP Basic (key as username) | Query params (`app_id` + `app_key`) |
| Pagination | skip-based (`resultsToSkip`) | page-number-based (1-indexed) |
| Rate limit | 10 req/min → 6 s delay | 50 req/min → 1.2 s delay |
| Date format | `DD/MM/YYYY` string | ISO-8601 UTC `YYYY-MM-DDTHH:MM:SSZ` |
| Salary type | `salaryType` field ("per day" / "per annum") | Annualised always; no rate-type signal |
| Salary confidence | Verbatim only | `salary_is_predicted` flag available |
| Location | `locationName` flat string | `location.display_name` nested |
| Employer | `employerName` flat | `company.display_name` nested |

### Live run (2026-06-15T10:53Z)

- 1 page fetched, **10 listings** returned, 10 stored in pipeline
- HTTP 200 throughout; `duration_ms = 1579`
- `run_manifest.json` confirms: `{"source": "adzuna", "count": 10, "error": null}`

### Tests (2026-06-15)

- **21 new tests** in `tests/adapters/test_adzuna.py` (all fixture-based, no live API calls)
- Full suite: **202 passed, 25 skipped, 0 failed** (baseline before this task: 181)

---

## 2026-06-15: v0.2 Query Slate — Tommy's Multi-Source Keyword Expansion (M1/M3/M4/M5/M6/M7)

**Task:** Implement Tommy's v0.2 query-slate spec (`.squad/decisions/inbox/tommy-v02-query-slate.md`).
All six work items (M1, M3, M4, M5, M6, M7) delivered in one sprint.

### Work items shipped

| Item | Change | File |
|---|---|---|
| M7 | `SourceConfig` gains `keywords_list: list[str]`; `model_validator` wraps legacy `keywords` scalar into single-item list; deprecation warning when both are set | `src/mechpm/config.py` |
| M1 | `ReedAdapter` iterates `keywords_list`, unions by `source_listing_id`, sleeps 6 s between ALL page requests (not just within-keyword) | `src/mechpm/adapters/reed.py`, `config.toml` |
| M3 | `AdzunaAdapter` gains `what_or`, `what_exclude`, `location0`, `category`, `max_pages` constructor params; `_fetch_page` uses `what_or` when non-empty | `src/mechpm/adapters/adzuna.py`, `config.toml` |
| M4 | `EnergyJoblineAdapter` gains `keywords_list`, `location`, `contract_type`, `max_pages_per_query`; `_build_ejl_search_urls()` standalone helper; adapter iterates search URLs, unions by `source_listing_id` | `src/mechpm/adapters/energy_jobline.py`, `config.toml` |
| M5 | `_RELEVANT_PATTERNS` expanded with `/engineering/`, `engineering[_-]manager`, `program[_-]manager`; `_MAX_JOBS` reduced from 100 → 50 | `src/mechpm/adapters/aviation_job_search.py` |
| M6 | `"adzuna"` inserted at index 1 in `_SOURCE_PRIORITY` (between `"reed"` and `"totaljobs"`) | `src/mechpm/extractor/dedup.py` |

### Live smoke results (2026-06-15T11:xx)

| Source | v0.1 fetched | v0.2 fetched | Notes |
|---|---|---|---|
| Reed | 29 | **201** | 4 queries × 100 cap, deduplicated; exceeded Tommy's 80-120 estimate — broad queries match large corpus |
| Adzuna | 10 | **200** | `what_or` OR semantics + `what_exclude` noise kill; 4 pages × 50 = 200 max. Tommy estimated 60-100. |
| Energy Jobline | 100 | **101** | 3 queries × 5 pages = 15 page fetches; EJL query results heavily overlap (same listings match all 3 keywords) → only +1 unique vs single-query. Tommy estimated 40-80 unique. |
| Aviation Job Search | 6 | **7** | `/engineering/` category absent from live sitemap structure; `engineering-manager` slug caught 1 extra. Consistent with low-volume specialist board. |

### Reed multi-query yield observations

- All 4 queries return close to 100 results each (full pages) → 400 raw results before within-source dedup
- After dedup by `jobId`, 201 unique → suggests ~50% overlap between queries (expected: "engineering project manager contract" overlaps strongly with "project manager mechanical")
- 6 s inter-request sleep respected across query boundaries — no 429 errors in 5 pages fetched
- Safety cap of 500 was not hit; the 201 count is the natural deduplicated union

### Adzuna OR-query behaviour

- `what_or` enables Adzuna's native OR semantics — far more recall than the previous `what` (AND) query
- 4 pages × 50 = 200 results returned at max capacity → confirms OR semantics dramatically broadens coverage
- `what_exclude` (software/devops/cloud/SAP/ERP/digital/IT) visibly removes IT noise at source level
- `location0=UK` + `category=engineering-jobs` applied simultaneously — no apparent conflicts
- The v0.1 adapter returned 10 listings; v0.2 returns 200 — 20× improvement in raw recall

### EJL pagination caveats from this expansion

- EJL keyword relevance ranking is not strict: "project manager" and "project manager HVAC" return heavily overlapping result sets because EJL appears to rank by relevance score, not exact keyword match
- The 3-query strategy yields only ~1% additional unique listings vs 1 query (101 vs 100) — diminishing returns from keyword variation on EJL
- `_build_ejl_search_urls()` correctly encodes keywords with `quote_plus` (spaces → `+`)
- 0-based pagination confirmed working: page 1 = no `&page=`, page 2 = `&page=1`, etc.
- 10 s crawl-delay fully respected between ALL requests (not just within a keyword)
- For v0.3: consider REDUCING to 2 queries instead of 3 (or fewer pages per query) since the overlap is high and 15 page fetches × 10 s = 150 s for marginal gain

### `_SOURCE_PRIORITY` reasoning for Adzuna's position

- Adzuna is a **first-party API** with structured data (not scraped HTML) — equally authoritative to Reed
- Reed at index 0, Adzuna at index 1: both are API sources; Reed is preferred as a tiebreaker because it's the UK's largest native job board (direct employer relationships)
- Adzuna at index 1 > Totaljobs (index 2) because Adzuna provides structured salary + employment type data from its API response; Totaljobs is HTML-scraped via StepStone platform
- Placing Adzuna before Totaljobs/CWJobs means when Reed and Adzuna both have the same listing, Reed's record is kept as canonical (correct: Reed has richer salary_type metadata). When Adzuna and Totaljobs both have the same listing, Adzuna is kept as canonical (correct: Adzuna's structured JSON is more reliable than scraped HTML).

### Tests (2026-06-15)

- **+62 new tests** across: `tests/test_config.py` (6 new), `tests/adapters/test_reed.py` (+7), `tests/adapters/test_adzuna.py` (+7), `tests/adapters/test_energy_jobline.py` (+5), `tests/adapters/test_aviation_job_search.py` (+7)
- Full suite: **264 passed, 25 skipped, 0 failed** (baseline 202; +62 new)
- All existing tests pass without modification

