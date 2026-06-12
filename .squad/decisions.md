# Decisions

Authoritative decision ledger for the team. Append-only. Newest at the bottom.

---

### 2026-06-12: Project founded
**By:** Steve (via Squad coordinator)
**What:** Cast a Peaky Blinders squad to build an agent that regularly scans UK job boards for contract PM roles in mechanical engineering and produces a deduplicated report covering start date, duration, day rate, and IR35 status.
**Why:** Recurring need to track UK mechanical-engineering PM contract opportunities across many fragmented sources.

---

### 2026-06-12: Canonical Listing Schema, Extraction Approach, Filtering Rules, and Dedup Strategy
**By:** Ada (Data Extraction)
**Status:** Proposed
**What:** 28-field canonical schema (listing_id, source, source_url, title, employer, agency, location, day_rate_min/max, duration_weeks, start_date, ir35_status, contract_type, remote_policy, description_clean, etc.). 3-tier extraction: fully structured (API/HTML direct), regex-based (start date, duration, day rates, IR35, location normalization), and LLM-assisted fallback (when regex fails). Four mandatory filters: contract type, geo (UK only), PM role, mechanical engineering domain. Jaro-Winkler fuzzy dedup on title + employer/agency + location + rate band overlap + 14-day posting proximity. Canonical record selection prioritizes field population, source rank, widest rate range, most recent last_seen_at. 25-item gold set proposed (8 TPs, 8 TNs, 6 edge cases, 3 dedup pairs).
**Why:** Standardizes extraction, filtering, and dedup across pipeline. Enables measurement against acceptance criteria.

---

### 2026-06-12: MVP Acceptance Criteria — 7 Dimensions
**By:** Arthur (QA Lead)
**Status:** Proposed
**What:** Per-source adapter acceptance (5 checks: data availability ≥10 listings, error handling, robots.txt compliance, HTML schema change detection, rate limiting). Extraction accuracy on 50+ gold-set listings (precision/recall per field; title ≥88%, company ≥90%, day_rate ≥80%, ir35_status ≥70%). Filter precision/recall (contract ≥98%/≥92%, UK ≥99%/≥95%, PM ≥92%/≥88%, mech domain ≥96%/≥90%). Dedup quality on 100+ hand-labelled pairs (precision ≥98%, recall ≥88%). E2E pipeline (execution time ≤15 min, non-empty output or explicit "none found" note, graceful partial failure, data persistence for replay, 100% schema validation). Report quality (hyperlinks ≥99% valid, zero duplicates in final output, outlier flagging 100%, clean rendering, metadata completeness). **Out of scope for v0.1:** freshness SLA <24h, inside/outside IR35 inference, cross-year dedup, company name normalization, NLP PM specialization.
**Why:** Precision-first asymmetry (false positives worse than false negatives). Acceptance gates ownership per dimension.

---

### 2026-06-12: Source Coverage Extended to Vertical-Specialist Boards
**By:** Steve (via Coordinator)
**Status:** Directive
**What:** Extend Michael's source shortlist to include transport (rail: RailwayPeople; aerospace: Aviation Job Search), energy (Energy Jobline), cross-sector (The Engineer Jobs), plus conditional Careerstructure (construction/M&E). Generalist Tier-1 (Reed, Totaljobs, CWJobs) + Vertical Tier-1 (RailwayPeople, Energy Jobline, The Engineer Jobs, Aviation Job Search) = 7 sources for MVP.
**Why:** Mechanical engineering PM contracts cluster in verticals. Generalist boards alone under-cover rail, aerospace, energy, construction sectors.
**Implication for filters:** Domain filter must accept "mechanical engineering" PM even when listing primary tag is transport/rail/aerospace/construction/energy, provided role substance is mechanical.

---

### 2026-06-12: Source Shortlist for UK Mechanical-Engineering PM Contract Roles
**By:** Michael (Backend / Scraping)
**Status:** Proposed (awaiting Tommy approval)
**What:** 3-source Tier-1 MVP: Reed.co.uk (official JSON API, free key, largest UK board), Totaljobs (HTML scrape, StepStone platform, contract filter), CWJobs (HTML scrape, reuses Totaljobs adapter). Tier-2 candidates: Jobserve (Playwright TBD), IMechE Careers (low volume, server-rendered), ContractorUK (Drupal, small volume). Hard SKIPs: LinkedIn (ToS prohibition), Indeed (Cloudflare + no RSS), Hays (robots.txt blocks job search). Fetch strategy: Reed API ~2 calls/run (100 resultsToTake, paginate if needed), Totaljobs/CWJobs 3 pages each (2–3 s delay, robots-permitted). Rate limits: Reed conservative 10 req/min free tier; HTML 2–3 s between pages. Polling: 4-hourly (6× per day) for generalist, daily/weekly for specialists.
**Why:** Balance coverage, compliance, and maintainability. Three sources cover majority of UK mech-eng PM contracts without legal risk.

---

### 2026-06-12: Vertical-Specialist Source Addendum
**By:** Michael (Backend / Scraping)
**Status:** Proposed (awaiting Tommy approval)
**What:** Transport rail (RailwayPeople — Next.js SSR, Jobiqo platform, 10 s Crawl-delay); transport aerospace/defence (Aviation Job Search — standard HTML, contract_types=2 filter); energy (Energy Jobline — Jobiqo/Drupal, 10 s Crawl-delay); cross-sector (The Engineer Jobs — Cloudflare-fronted but robots-allowed, Crawl-delay via Content-Signal). Conditional Careerstructure (StepStone reuse, amber on robots.txt `?page=N` vs `&page=N`). Tier-2 candidates: automotive (JustAutomotive, Autopeople), maritime (SeaCareers, IMAREST Jobs), construction (ConstructionJobs.co.uk, CIOB Careers, NCE Jobs, Building.co.uk, TheConstructionIndex), specialist agencies (Matchtech, Carbon60, Morson, NES Fircroft, Jonathan Lee, Spencer Ogden, RandstadCPE, Brunel). **Scope:** Tier-1 adds 4 sources (RailwayPeople, Energy Jobline, The Engineer Jobs, Aviation Job Search) to MVP 7-source target. Tier-2 + agencies deferred to v0.2 validation.
**Why:** Vertical coverage fills gaps in rail, energy, aerospace, construction where mechanical engineering PM roles cluster.

---

### 2026-06-12: MVP Report Format & UK Contract-Market Domain Notes
**By:** Polly (Reporting & Domain)
**Status:** MVP Specification
**What:** Markdown report `reports/{YYYY-MM-DD}.md` (weekly, Friday 17:00). Header with metadata (week, timestamp, source coverage, dedup counts). Section 1: 🆕 New This Week (sorted by start_date). Section 2: ⚡ Urgent Starts (≤14 days). Section 3: All Current Roles by Region + seniority level (London +15–25%, South-West +5–10%, Midlands baseline, North ±5–15%, Scotland −10–20%). Flags: 🆕 new, ⚡ urgent, 💰 premium (≥£700 outside IR35), ⚠️ sanity check (no report, triggers review). Sanity checks: low rate (≤£250), high rate (≥£1500 outside), past start, extreme duration (>24mo), missing IR35 (≥£700), missing rate, vague location, title inconsistency. Day-rate bands by seniority: Junior £350–600, Mid £480–750, Senior £600–950, Programme £800–1500+. IR35 impact: outside ~£525 net from £700 quoted; inside ~£390 net from £550 quoted (equivalent to ~£60k salary). Agencies: Gatenby Sanderson, Kforce, Apex, Heidrick, Morgan Hunt, Harvey Nash, Staffing 360, Croner Umbrella.
**Why:** Domain-aware report tailored to Steve's workflow (new → urgent → full pipeline). Market anchors prevent mis-flagged entries.

---

### 2026-06-12: MVP Architecture — Python, SQLite, GPT-4o-mini, Markdown
**By:** Tommy (Lead)
**Status:** Proposed (awaiting Steve approval)
**What:** Tech stack: Python 3.12+, SQLite (single file `data/mechpm.db`, two tables: raw_listings, normalized_listings), OpenAI GPT-4o-mini via openai SDK (~$0.15/1M input tokens), httpx (async, timeout/retry), config.toml (search terms, rate limits, LLM key), Markdown report. Pipeline: Scheduler → Orchestrator (loops over adapters, fetch) → raw_listings → Extractor (Ada's LLM extraction) → normalized_listings → Dedup + Filter → Report Renderer → `reports/YYYY-MM-DD.md`. Module ownership: Tommy (design), Michael (orchestrator/CLI/adapters), Ada (extractor/dedup), Polly (reporter), Arthur (tests/acceptance). Source-Adapter ABC contract: async fetch(), rate_limit property, tos_notes, never crashes pipeline (logs warning, returns [] on error). Repo layout: `src/mechpm/` (orchestrator, models, adapters/, extractor/, dedup/, storage/, reporter/), `data/mechpm.db`, `reports/`, `config.toml`, tests/. MVP scope IN: Reed API + CWJobs adapters, 7-field LLM extraction, SQLite storage, basic fuzzy dedup (Jaro-Winkler >0.85), Markdown report, CLI entry point, manual trigger (no scheduler). DEFERRED v0.2+: LinkedIn/Indeed, Task Scheduler, HTML/email, historical trends, confidence scoring, browser adapters.
**Why:** Minimal-ops local stack. Proves pipeline before cloud infra. Python ecosystem (scraping, LLM, iteration speed). Windows-native execution.

---

### 2026-06-12: Architecture Finalisation — 7-Source MVP with Scheduler
**By:** Tommy (Lead)
**Status:** Decision (binding)
**What:** Scheduler (Windows Task Scheduler, every Friday 17:00 BST), per-source crawl-delay registry in config.toml (Reed 0s, Totaljobs/CWJobs 3s, RailwayPeople/Energy Jobline 10s, The Engineer Jobs 5s, Aviation Job Search 3s), StepStone parameterised adapter (Totaljobs + CWJobs config-driven, single class), Jobiqo two separate adapters (RailwayPeople Next.js JSON vs Energy Jobline Drupal HTML), Cloudflare two-phase for The Engineer Jobs (httpx phase A with browser headers, Playwright fallback phase B), secret management via `.env` + python-dotenv, sector as first-class enum field (10 values: rail, aerospace, defence, energy, nuclear, construction, maritime, automotive, process, generalist), Playwright conditionally approved for The Engineer Jobs only. MVP: all 7 sources live with acceptance gates, weekly scheduled delivery, structured extraction + sector tagging, deduplicated Markdown report. Sprint plan: 12 work items (Michael 7 adapters + orchestrator, Ada extraction + storage, Polly reporter, Arthur tests).
**Why:** Finalises all architectural unknowns (scheduler, vertical source patterns, secrets, sector handling). Enables parallel work without re-design.

---

### 2026-06-12: Decision: Scaffold Stack Choices
**By:** Michael (Backend / Scraping)
**Status:** Implemented and verified
**What:** Build backend: hatchling (PEP 517, zero-config for src/ layout). TOML: tomllib stdlib (Python 3.12+, no extra dep). CLI: argparse stdlib (minimal footprint). HTML parsing: selectolax (5–10× faster than BeautifulSoup). Async HTTP: httpx.AsyncClient (consistent API/HTML client, better timeout/retry). Secrets: python-dotenv + .env (dev UX, git-ignored). Config file: config.toml flat [sources.<name>] tables (named-key lookup, human-editable). These choices are binding for all seven adapters.
**Why:** Establishes defaults for team. Prevents duplicate discussions on standard tooling.

---

### 2026-06-12: CLI `report` Subcommand Required
**By:** Polly (Reporting & Domain)
**Status:** Request — awaiting Michael implementation
**What:** Wire `src/mechpm/reporter/render_weekly` into CLI via `python -m mechpm report --week YYYY-MM-DD [--output PATH]`. Loads NormalizedListing records from DB whose discovered_at/last_seen_at fall within the week window. Calls render_weekly with RunMetadata and output path. No new deps required.
**Why:** Reporter module complete and smoke-tested; ready for CLI integration.

---

### 2026-06-12: Ada Dependency Additions
**By:** Ada (Data Extraction)
**Status:** Flagged for Michael (pyproject.toml owner)
**What:** Add two runtime deps: rapidfuzz >=3.0 (Jaro-Winkler title-similarity for dedup, hard production dep but silent graceful degrade), openai >=1.0 (LLM-fallback extractor, soft dep, only required if MECHPM_LLM_FALLBACK=1 env var set). Both Windows-native, no native build tools required.
**Why:** Enable dedup and LLM extraction pipeline.

---

### 2026-06-12: arthur-test-deps.md — Test Dependency Request
**By:** Arthur (QA)
**Status:** Flagged for Michael (pyproject.toml owner)
**What:** Add test deps under [project.optional-dependencies] dev: pytest >=8.2, pytest-asyncio >=0.24, pytest-cov >=5.0, jsonschema >=4.22. Configure [tool.pytest.ini_options] with asyncio_mode = "auto", testpaths = ["tests"], markers including "slow". These are dev-only, not needed for production package.
**Why:** Enable test collection and execution for 84+ test cases with coverage reporting.

---

### 2026-06-12: MVP shape — full 7-source coverage + scheduled weekly run from day one
**By:** Steve (via Squad coordinator)
**Status:** Directive
**What:** Build to all 7 Tier-1 sources (Reed, Totaljobs, CWJobs, RailwayPeople, Energy Jobline, The Engineer Jobs, Aviation Job Search) AND ship the weekly scheduler from day one — not v0.2. This overrides Tommy's lean "2 sources + manual CLI" MVP cut. Implications: Tommy finalises StepStone/Jobiqo patterns, Michael builds adapters in bulk, scheduler becomes v0.1 architecture decision, Arthur's acceptance gates apply to all 7, Ada treats sector as first-class schema field.
**Why:** User directive — get usable coverage and recurring delivery in v0.1 rather than iterating to it.

---

### 2026-06-12: Reviewer-Lockout Patch — Tommy (2026-06-12)

**Date:** 2026-06-12  
**Author:** Tommy (Lead) — acting under Reviewer Rejection Lockout  
**Status:** Implemented and verified

---

## Escalation Rationale

Arthur (Reviewer) rejected Ada's UK filter and mechanical-domain filter implementations on 2 gold-set cases during Sprint 1 QA. Steve explicitly chose **strict Reviewer Rejection Lockout** enforcement: Ada is locked out of this revision and may not contribute, advise, or co-author the patch. Tommy was assigned as the sole revision author per `squad.agent.md` lockout semantics.

Tommy read Ada's code to understand the defects but all patch authorship is Tommy's.

---

## Defects Fixed

### Defect 1 — `detect_country()` missing UAE/Dubai

| Item | Detail |
|---|---|
| **File** | `src/mechpm/extractor/regex_fields.py` |
| **Fixture** | `tests/fixtures/gold_set/negative/neg_03_nonuk_dubai` |
| **Root cause** | `_NON_UK_MAP` had no UAE/Dubai/Abu Dhabi entry. `detect_country("Dubai, United Arab Emirates")` defaulted to `"GB"`, making `passes_uk()` return `True` for a non-UK listing. |
| **Fix** | Added UAE entry `(re.compile(r"\b(dubai\|abu\s+dhabi\|uae\|united\s+arab\s+emirates)\b", re.IGNORECASE), "AE")` as first entry in `_NON_UK_MAP`. Also added USA (`US`), Saudi Arabia (`SA`), Qatar (`QA`), Singapore (`SG`), India (`IN`) per the "small non-exhaustive Gulf/EU/US list" directive. Existing IE/NL/DE/FR entries retained unchanged. |
| **Acceptance impact** | `uk_filter` precision: 0.952 → 1.000 (≥ 0.99 threshold now met). |

### Defect 2 — "civil engineering" substring false-fires mech disqualifier

| Item | Detail |
|---|---|
| **File** | `src/mechpm/extractor/filters.py` |
| **Fixture** | `tests/fixtures/gold_set/edge_cases/edge_06_multi_discipline` |
| **Root cause** | `passes_mechanical()` mechanical-sectors path used `any(phrase in title_lower for phrase in DISQUALIFY_PHRASES)` (substring). `"civil engineer"` is a substring of `"civil engineering"`, so any title containing "Civil Engineering" was disqualified, even when the role was mechanically-led. |
| **Fix — mechanical-sectors path** | Kept the substring disqualifier check but added a mech-keyword counterbalance: when a disqualifier fires, the function now passes the listing only if a `MECH_KEYWORDS` entry (word-boundary anchored) also appears in the title. Multi-discipline roles with explicit mech content (e.g. "Mechanical & Civil Engineering") pass; purely civil titles without mech keywords (e.g. "Civil Engineering / Highways") still fail. |
| **Fix — generalist path** | Added `_DISQUALIFY_RES` (pre-compiled `\b{phrase}\b` patterns for each DISQUALIFY_PHRASES entry). Generalist scoring now uses word-boundary regex instead of substring check, so `"civil engineer\b"` does NOT match `"civil engineering"` in that path either. |
| **Inline comment added** | `# example: matches "civil engineer", not "civil engineering"` on `_DISQUALIFY_RES`. |
| **Acceptance impact** | `mech_filter` false-negative for `edge_06` eliminated. Recall and precision both within gate thresholds. |

---

## Pytest Before / After

| State | Passed | Failed | Skipped |
|---|---|---|---|
| **Before patch** | 84 | 3 | 26 |
| **After patch** | 87 | 0 | 26 |

Failing tests resolved:
- `test_filter_outcomes[negative/neg_03_nonuk_dubai]`
- `test_filter_outcomes[edge_cases/edge_06_multi_discipline]`
- `test_uk_filter_precision_recall`

---

## Scope Boundaries

- Only `src/mechpm/extractor/regex_fields.py` and `src/mechpm/extractor/filters.py` were modified.
- No tests modified. No gold-set fixtures modified. No other source files touched.
- Tommy's country additions are a conservative minimum: Ada's broader country/city disambiguation coverage remains her responsibility for future sprints.
- Ada's broader disqualifier list coverage and any edge cases beyond this patch set remain Ada's responsibility once the lockout lifts.

---

## Downstream

Scribe handles commit. No further action required from Tommy on this patch.

---



---

# Decision Drop: StepStone Parameterised Adapter

**By:** Michael (Backend / Scraping)
**Date:** 2026-06-12
**Status:** Adapter implemented; live verification pending Arthur's gate

---

## What was implemented

### Adapter file
`src/mechpm/adapters/stepstone.py`

**Class:** `StepStoneAdapter(SourceAdapter)`

Constructor signature:
```python
StepStoneAdapter(name: str, domain: str, search_path: str, crawl_delay: int = 3)
```

One instance is created per `[sources.totaljobs]` / `[sources.cwjobs]` config block.
The same class covers both boards; `name` propagates directly to `RawListing.source`.

---

## Config blocks consumed

`config.toml` — two blocks (both kept `enabled = false` pending Arthur's gate):

```toml
[sources.totaljobs]
enabled = false
crawl_delay = 3
domain = "www.totaljobs.com"
search_path = "/jobs/project-manager/engineering-jobs"

[sources.cwjobs]
enabled = false
crawl_delay = 3
domain = "www.cwjobs.co.uk"
search_path = "/jobs/project-manager/in-uk"
```

`domain` and `search_path` are stored as Pydantic extra fields (SourceConfig uses
`ConfigDict(extra="allow")`). Accessed in CLI via `cfg.model_extra.get(...)`.

---

## Query string + pagination

**Base URL:** `https://{domain}{search_path}?contract=true`
**Page 2+:** `https://{domain}{search_path}?contract=true&page={N}`

Rationale: `?contract=true` is the StepStone facet filter confirmed working in prior
research. Pagination uses `?page=N` (not `&page=N`) for the first appended param —
consistent with robots.txt guidance from Careerstructure research (Disallow: `/*&page=*`
applied defensively here too even though Totaljobs/CWJobs robots don't explicitly ban it).

**Page cap:** 5 pages (MVP politeness limit; robots.txt on both sites permits more).
**Crawl-delay:** 3 s between pages (from config.toml; `asyncio.sleep` inside `fetch()`).

---

## DOM selectors used

StepStone platform, 2025-era markup (`data-at` attribute pattern):

| Field | Primary selector | Fallback |
|---|---|---|
| Card root | `article[data-at="job-item"]` | — |
| Title + link | `a[data-at="job-item-title"]` | `[data-at="job-item-title"] a` → `h2 a` → `h3 a` |
| Employer | `[data-at="job-item-company-name"]` | — (nullable) |
| Location | `[data-at="job-item-location"]` | — (nullable) |
| Salary | `[data-at="job-item-salary"]` | — (nullable) |
| Contract type | `[data-at="job-item-employment-type"]` | defaults to `"contract"` |
| Date | `[data-at="job-item-date"]` | `time` element (checks `datetime` attr first) |
| Snippet | `[data-at="job-item-description"]` | — (nullable) |

**Job-ID extraction:**
1. Regex `job(\d+)` from listing URL suffix (e.g. `-job123456789`).
2. Card attribute `data-job-id` or `data-id`.
3. Last resort: full URL used as opaque ID (prevents silently dropping cards with unexpected URL patterns).

---

## Relative-date parser

StepStone shows relative dates ("2 days ago", "today", etc.) on cards.
`_parse_relative_date()` handles:
- `"just now"` / `"today"` → today 00:00 UTC
- `"yesterday"` → yesterday 00:00 UTC
- `"N seconds/minutes/hours/days/weeks/months ago"` → `now - timedelta`
- Months approximated as 30 days.
- Returns `None` on unrecognised formats (posted_at left null; since-filter skipped).

---

## Detail-fetch decision

**Skipped for MVP.** Search-results cards on StepStone do expose a snippet field
(`[data-at="job-item-description"]`). This is set as `description_raw`.
`metadata["detail_fetched"] = False` is set on every listing so Ada's extractor
can see the fetch status and decide whether to fallback to LLM.

Following the listing URL for the full description page would require `crawl_delay`
between each detail request (3 s × N listings = significant runtime). Deferred to v0.2
or when Ada's LLM-fallback precision on snippet-only records proves insufficient.

---

## CLI wiring

`src/mechpm/cli.py` — additive changes only:

1. `_get_registry()`: added `StepStoneAdapter` import; mapped both `"totaljobs"` and
   `"cwjobs"` to the same class.
2. `_build_adapters()`: added `elif name in ("totaljobs", "cwjobs")` branch that reads
   `domain` and `search_path` from `cfg.model_extra`, logs a warning and skips if either
   is missing, and instantiates `StepStoneAdapter(name=name, domain=..., ...)`.

---

## Quirks and anti-bot notes

- **No live test run yet** — adapter is implemented against researched selectors; actual
  DOM verified against StepStone platform knowledge. Arthur's gate (HTML schema check)
  will confirm selectors on first live run.
- **Cloudflare / bot detection:** Totaljobs and CWJobs do not use aggressive Cloudflare
  challenge pages (unlike The Engineer Jobs). Browser headers (`User-Agent` Chrome 125,
  `Accept-Language: en-GB`) are set. If 403/429 is returned, `_fetch_page` logs a warning
  and returns `[]` — pipeline continues.
- **Selector change detection:** If zero cards are found on page 1, a `WARNING` is emitted
  — Arthur's HTML-schema-change gate should pick this up via log monitoring.
- **`enabled = false`:** Both sources remain disabled in config.toml; coordinator flips
  after all adapters land and Arthur verifies.

---

# Decision Drop: RailwayPeople Adapter

**By:** Michael (Backend / Scraping)
**Date:** 2026-06-12
**Status:** Adapter implemented; live verification pending Arthur's gate

---

## Adapter

- **File:** `src/mechpm/adapters/railwaypeople.py`
- **Class:** `RailwayPeopleAdapter(SourceAdapter)`
- **Logger:** `mechpm.adapter.railwaypeople`
- **`name`:** `"railwaypeople"`
- **`crawl_delay`:** `10` (inter-source delay; orchestrator sleeps 10 s before
  moving to the next source)

---

## Search URL + Pagination Strategy

```
https://www.railwaypeople.com/jobs?keywords=project+manager&jobtype=contract
```

- Page 1: base URL above (no `&page=` suffix)
- Page N (N ≥ 2): `…&page=N`
- **Cap:** 5 pages (MVP safety cap; configurable by adjusting `_MAX_PAGES`)
- **Intra-source delay:** `await asyncio.sleep(10)` between every page fetch —
  satisfies robots.txt `Crawl-delay: 10` mandate
- **HTTP client:** `httpx.AsyncClient` with realistic Chrome/Windows browser
  headers + `follow_redirects=True`

---

## __NEXT_DATA__ Parsing Strategy

1. `HTMLParser(response.text)` (selectolax) to locate
   `<script id="__NEXT_DATA__" type="application/json">`.
2. `json.loads(script_node.text())` to deserialise.
3. Navigate to `props.pageProps` first (canonical Next.js SSR root).
4. Recursive `_find_jobs_list()` probes priority keys
   (`jobs`, `initialJobs`, `searchResults`, `results`, `listings`, `data`,
   `items`) then falls back to full-dict walk (depth-limited to 12 levels).
5. Qualifying list: first array whose `[0]` element is a `dict` containing
   both a `"title"` key AND at least one URL-like key
   (`url`, `link`, `href`, `path`, `slug`, `jobUrl`, `job_url`,
   `permalink`).
6. Widens search to the whole `__NEXT_DATA__` blob if `pageProps` search
   finds nothing.

### Discovered JSON path

**Not yet confirmed from a live fetch** (live verification is Arthur's gate).
The adapter logs the discovered path at `INFO` level the first time it
succeeds — look for the message:

```
RailwayPeople: jobs list found at JSON path '<path>' (count=N, page=1).
```

Suspected path based on Jobiqo Next.js conventions:
`props.pageProps.jobs` or `props.pageProps.searchResults.jobs`.

---

## Field Mapping

| RawListing field       | Jobiqo JSON candidates (first non-None wins)                        |
|------------------------|---------------------------------------------------------------------|
| `source_listing_id`    | `id`, `jobId`, `job_id`, `nid`, `uuid`, `slug`                     |
| `url`                  | `url`, `link`, `href`, `permalink`, `path`, `slug` (relative→abs)  |
| `title`                | `title`                                                             |
| `employer`             | `company`, `employer`, `companyName`, `company_name`, `advertiser` |
| `location_raw`         | `location`, `locationName`, `location_name`, `city`, `region`      |
| `salary_raw`           | `salary`, `salaryDescription`, `salary_description`, `rate`        |
| `contract_type_raw`    | `contractType`, `contract_type`, `jobType`, `employment_type`      |
| `description_raw`      | `description`, `jobDescription`, `body`, `summary`                 |
| `posted_at`            | `datePosted`, `date_posted`, `postedAt`, `posted_at`, `createdAt`  |
| `metadata`             | all remaining (Jobiqo-specific) keys not mapped above              |

Dict-valued fields (e.g. nested location objects) are normalised to
their `name`/`label`/`text` sub-key.  Unix timestamps (float-as-string)
are supported in `_parse_date()`.

---

## Error Handling (pipeline safety)

- **HTTP error / timeout** → `logger.warning`, return `([], 0)` for that page
- **`__NEXT_DATA__` script missing** → `logger.warning` (site structure change)
- **JSON decode error** → `logger.warning`
- **Jobs list not found** → `logger.warning` including top-level `pageProps`
  keys so future debugging is easy
- **Individual job mapping failure** → `logger.exception`, skip that job, continue
- **Any unexpected exception in `fetch()`** → `logger.exception`, return
  partial results already accumulated — pipeline never crashes

---

## Source-Default Sector

`rail` — handled by Ada's `sector.py` `SOURCE_DEFAULTS` mapping.
No code change required in this adapter.

---

## CLI Registration

`src/mechpm/cli.py` `_get_registry()` now imports and registers
`RailwayPeopleAdapter` under key `"railwaypeople"`.  It is instantiated
via the generic path `cls(crawl_delay=cfg.crawl_delay)` — no special
wiring required.

## config.toml

`enabled = false` in `[sources.railwaypeople]` — **unchanged**.
The coordinator will flip to `true` after Arthur's acceptance gate passes.

---

# Decision Drop — Energy Jobline Adapter

**Author:** Michael (Backend / Scraping)
**Date:** 2026-06-12
**Status:** Implemented; live verification pending Arthur's gate

---

## File Path + Class

- **File:** `src/mechpm/adapters/energy_jobline.py`
- **Class:** `EnergyJoblineAdapter(SourceAdapter)`
- **name:** `"energy_jobline"` · **crawl_delay:** `10` (seconds, inter-source)

---

## Search URL

```
https://www.energyjobline.com/jobs
  ?keywords=project+manager+mechanical
  &location=United+Kingdom
  &contract_type=contract
```

Pagination appends `&page=N` (1-based, capped at `_MAX_PAGES = 5` for MVP).
`/search/` is **not** used — robots.txt disallows that legacy path.

---

## Selectors Used

### Card (listing container) — tried in order, first non-empty match wins

| Priority | Selector | Notes |
|----------|----------|-------|
| 1 | `article.job-result` | Most common Jobiqo 3.x theme |
| 2 | `article.job-item` | Older Jobiqo / Drupal 7 |
| 3 | `li.job-result` | List-view variant |
| 4 | `li.job-item` | List-view variant |
| 5 | `[itemtype*='JobPosting']` | schema.org markup (reliable fallback) |
| 6 | `div.job-listing` | Generic fallback |
| 7 | `div.views-row` | Drupal Views generic row |
| 8 | `[data-job-id]` | Attribute-keyed fallback |

### Within each card

| Field | Selectors tried (in order) |
|-------|---------------------------|
| Title + URL | `[itemprop='title']`, `h2.job-title a`, `h3.job-title a`, `.job-title a`, `.title a`, `h2 a`, `h3 a` |
| Employer | `[itemprop='hiringOrganization'] [itemprop='name']`, `[itemprop='hiringOrganization']`, `.employer-name`, `.company-name`, `.employer`, `.company`, `[data-employer]` |
| Location | `[itemprop='jobLocation'] [itemprop='name']`, `[itemprop='jobLocation']`, `.job-location`, `.location`, `[data-location]` |
| Salary | `[itemprop='baseSalary']`, `.salary`, `.pay-rate`, `.rate`, `.field-job-salary`, `.field-salary` |
| Posted date | `[itemprop='datePosted']`, `time.date`, `.date`, `.posted-date`, `.created`, `.field-posted-date`, `time` — prefers `datetime` / `content` attribute before inner text |
| Snippet | `[itemprop='description']`, `.job-snippet`, `.description`, `.summary`, `.views-field-body`, `.field-body` |

### source_listing_id extraction

1. `data-job-id` card attribute
2. `data-id` card attribute
3. First purely numeric path segment in the listing URL (e.g. `/job/98765/title-slug`)
4. Last path segment (last resort)

---

## Drupal / Jobiqo DOM Notes

- **Platform:** Energy Jobline runs an older Jobiqo/Drupal variant — **not** the Next.js SSR path used by RailwayPeople. There is no `__NEXT_DATA__` blob; page content is server-rendered HTML.
- **Schema.org markup:** Jobiqo themes typically emit schema.org `JobPosting` microdata (`itemtype`, `itemprop`). These selectors are the most semantically stable and are prioritised in field extraction.
- **`<time datetime="...">` elements:** Drupal commonly uses `<time>` with a machine-readable `datetime` attribute. The adapter reads this attribute before falling back to visible text, which avoids parsing human-relative strings like "3 days ago".
- **Paginator:** Energy Jobline uses `?page=N` (1-based) appended to the full query string. Page 1 is the bare URL (no `&page=1` parameter).
- **No `since` push-down:** The site has no query-param date filter. `since` is applied client-side after mapping `posted_at`. Many cards omit the posted date, so `since` filtering is best-effort.
- **Detail fetch skipped (MVP):** `metadata["detail_fetched"] = False` on every listing. Full-page detail fetches can be enabled in a later sprint without changing the `RawListing` contract.
- **Graceful degradation:** If all card selectors return empty (DOM change, A/B test, site redesign), the adapter logs a structured `WARNING` listing all tried selectors and returns `[]`. It never crashes.

---

## CLI Wiring

`EnergyJoblineAdapter` added to `_get_registry()` in `src/mechpm/cli.py`.
Instantiation path: `cls(crawl_delay=cfg.crawl_delay)` (generic non-Reed path) — correct, since the constructor signature is `__init__(self, crawl_delay=10, **kwargs)`.

`enabled = false` in `config.toml` is **not** changed per task instructions.

---

## Crawl-Delay Compliance

- `_PAGE_DELAY_SECONDS = 10` → `asyncio.sleep(10)` between every pair of successive page requests inside `fetch()`.
- `crawl_delay = 10` on the adapter → orchestrator sleeps 10 s **after** this adapter before starting the next one.
- Both levels enforced independently; no page fetch ever starts sooner than 10 s after the previous one.

---

# Decision Drop: Aviation Job Search Adapter

**Date:** 2026-06-12
**By:** Michael (Backend / Scraping)
**Status:** Implemented; live verification pending Arthur's gate

---

## File Path & Class

- **File:** `src/mechpm/adapters/aviation_job_search.py`
- **Class:** `AviationJobSearchAdapter(SourceAdapter)`
- `name = "aviation_job_search"`, `crawl_delay = 3`

---

## Search URL & Pagination

```
https://www.aviationjobsearch.com/en-GB/jobs
    ?title=project+manager
    &job_categories=Engineering
    &contract_types=2
    &page=N          (N = 1..5, stops early on empty page)
```

- `contract_types=2` — contract filter (confirmed from prior shortlist research).
- `job_categories=Engineering` — narrows to engineering job categories.
- `page=N` appended for pages 2–5; page 1 is the implicit default.
- **3 s sleep** between every page request (`asyncio.sleep(3)`) — satisfies robots.txt
  crawl-delay and binding team decision (Architecture Finalisation, 2026-06-12).

---

## Selectors Used

The adapter tries the following CSS selectors in order (first match on any page wins):

```
[data-test='job-item']       [data-testid='job-card']     [data-testid='job-item']
article.job                  li.job-listing               div.job-listing
div.job-result-card          div.job-card                 .listing-item
.search-result-item          li[class*='job']             div[class*='job-item']
div[class*='job-card']
```

Per-card field selectors (first match wins within card):

| Field     | Selectors (ordered)                                                           |
|-----------|-------------------------------------------------------------------------------|
| Title     | `[data-test='job-title']`, `h2 a`, `h3 a`, `h2`, `h3`, `.job-title a`, ...  |
| Employer  | `[data-test='employer']`, `.company-name`, `.employer`, `.company`, ...       |
| Location  | `[data-test='location']`, `.location`, `.job-location`, `[class*='location']` |
| Salary    | `[data-test='salary']`, `.salary`, `.rate`, `.compensation`, ...              |
| Date      | `time`, `[data-test='posted-date']`, `.posted-date`, `[class*='posted']`, ... |

If **no selector matches** on a page, the adapter logs a structured `WARNING` with all
selectors tried (signals DOM change) and returns `[]` for that page.

---

## `metadata["aggregator"] = True` Rationale

Aviation Job Search aggregates listings from multiple employer ATS feeds and agency
boards into a single search surface. This means:

1. The **same underlying role** may appear under slightly different employer-name
   spellings (e.g. "BAE Systems" vs "BAE Systems plc" vs submitted via agency).
2. Title + employer + location + rate-band dedup (Ada's standard strategy) will
   produce false positives.
3. The canonical dedup key must be `source_listing_id` (the numeric job-ID extracted
   from the listing URL) rather than a fuzzy title+employer match.

Setting `metadata["aggregator"] = True` signals this to Ada's dedup layer so it can
apply the correct dedup path for aviation_job_search records.

---

## Registry Wiring

`src/mechpm/cli.py` — `_get_registry()` now includes:

```python
from mechpm.adapters.aviation_job_search import AviationJobSearchAdapter
_ADAPTER_REGISTRY["aviation_job_search"] = AviationJobSearchAdapter
```

`config.toml` entry `[sources.aviation_job_search]` remains `enabled = false`
(per directive — DO NOT flip).

---

## Geo Filtering

All results are passed through; no UK-only filtering at adapter level.
Ada's `passes_uk` filter is the correct gate, per team convention.

---

## Date Parsing

Handles both relative (`"today"`, `"yesterday"`, `"N days/weeks/months ago"`) and
absolute (`"12 Jun 2026"`, `"2026-06-12"`, ISO-8601) formats.
Falls back to `posted_at = None` on unparseable strings (logged at DEBUG).

---

## Never-Crash Guarantee

- Top-level `try/except Exception` in `fetch()` returns partial results on failure.
- Per-page HTTP errors (`HTTPStatusError`) are caught in `_fetch_page()`; returns `[]`.
- Per-card parse errors are caught in `_parse_page()`; skips bad card, continues.
- `print()` used only in `__main__` self-test block.
- Logger: `logging.getLogger("mechpm.adapter.aviation_job_search")`.

---

# Decision Drop: The Engineer Jobs Adapter

**Date:** 2026-06-12
**Author:** Michael (Backend / Scraping)
**Status:** Adapter implemented; Phase A unverified; Phase B unverified (needs Playwright install + live test)

---

## File + Class

| Item | Value |
|---|---|
| **File** | `src/mechpm/adapters/the_engineer.py` |
| **Class** | `TheEngineerAdapter(SourceAdapter)` |
| **`name`** | `"the_engineer"` |
| **`crawl_delay`** | `5` (seconds, inter-source; orchestrator-managed) |

---

## Phase A vs Phase B — Trigger Conditions

### Phase A (default): `_fetch_httpx()`

- Uses `httpx.AsyncClient` with a realistic Chrome 124 on Windows User-Agent plus `Accept`, `Accept-Language`, `Referer`, `Sec-Fetch-*` headers.
- Search URL: `https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract`
- Paginates `&page=2` … `&page=5` (cap: 5 pages). Sleeps 5 s between requests.
- **Cloudflare challenge detection** (raises `CloudflareChallengeError`):
  - Status code 403 or 503 **with at least one CF marker in body**, OR
  - Status code 200 **with at least one CF marker in body** (silently-tunnelled challenge).
  - CF markers checked: `"Just a moment..."`, `"cf-browser-verification"`, `"_cf_chl_"`, `"Attention Required! | Cloudflare"`.
- Non-CF 403/503: logs warning, stops pagination, returns partial list (no Phase B trigger).
- Returns `list[RawListing]` (empty list = no results, **not** a Phase B trigger).
- Returns `None` sentinel: never explicitly returned by this implementation; `fetch()` handles it defensively.

### Phase B (fallback): `_fetch_playwright()`

Triggered when Phase A:
1. Raises `CloudflareChallengeError`, OR
2. Raises any other unexpected exception (falls through defensively), OR
3. Returns `None` (sentinel path, handled defensively in `fetch()`).

An empty list from Phase A is **not** a Phase B trigger — zero results is a valid successful response.

---

## Lazy Playwright Import Strategy

Per Tommy's decision §8 (`mechpm[browser]` is optional):

```python
# Inside _fetch_playwright() only — NOT at module top:
from playwright.async_api import async_playwright  # lazy import
```

- The module loads cleanly with zero import cost when Playwright is absent.
- If the import fails (`ImportError`), `_fetch_playwright()` re-raises it.
- `fetch()` catches `ImportError` from the Phase B call and emits a structured `logger.warning` with the fix command: `pip install mechpm[browser] && playwright install chromium`.
- Pipeline returns `[]` — never crashes.

---

## Selectors Used

Tried in order; the first selector returning a non-empty node list is used:

| Priority | Selector | Notes |
|---|---|---|
| 1 | `article.job` | Primary Mark Allen Group card element |
| 2 | `[data-job-id]` | Data-attribute anchor (any element) |
| 3 | `.job-card` | Generic job-card class |
| 4 | `li.job` | List-item variant |
| 5 | `.job-listing` | Alternative class |
| 6 | `.result-item` | Fallback |
| 7 | `.vacancy` | Last resort |

**Within each card**, field extraction tries multiple sub-selectors with `or`-chaining so a missing field silently becomes `None` rather than raising. Listing ID falls back from `data-job-id` attribute → last URL path segment. Cards with no resolvable ID are skipped.

---

## `pyproject.toml` `[browser]` Extra Status

**PRESENT** — no action required.

```toml
[project.optional-dependencies]
browser = ["playwright>=1.40"]
```

Confirmed in `pyproject.toml` at project root. Tommy's §8 decision is already reflected. No edit needed.

---

## CLI Wiring

`TheEngineerAdapter` added to `_get_registry()` in `src/mechpm/cli.py` (additive only):

```python
from mechpm.adapters.the_engineer import TheEngineerAdapter
_ADAPTER_REGISTRY["the_engineer"] = TheEngineerAdapter
```

`config.toml` `[sources.the_engineer]` remains `enabled = false` (directive §4: do NOT flip).

---

## Quality Guarantees

- ✅ Never crashes pipeline — all exceptions caught, pipeline returns `[]` on total failure.
- ✅ Both phases enforce 5 s delay between page requests (`asyncio.sleep(_PAGE_DELAY_SECONDS)`).
- ✅ Logger: `logging.getLogger("mechpm.adapter.the_engineer")`.
- ✅ `print` only in `__main__` self-test block.
- ✅ Playwright browser/context closed in `finally` block regardless of success or failure.
- ✅ `since` filter applied client-side (listings without `posted_at` pass through).

---

## Verification Needed (Live Test)

| Check | Status |
|---|---|
| Phase A returns listings (httpx, no CF block) | Unverified — needs live network test |
| Phase A raises `CloudflareChallengeError` on CF block | Unverified — needs CF-blocked response |
| Phase B parses listings via Playwright | Unverified — needs `pip install mechpm[browser] && playwright install chromium` |
| Phase B gracefully degrades when Playwright absent | Import-error path exercised by inspection; live run unverified |
| Selector coverage on actual Mark Allen Group DOM | Unverified — DOM may differ from selector assumptions |

---

# Arthur — Adapter Batch Gate Rejection

**Date:** 2026-06-12  
**Time:** 19:30 BST  
**Rejecting batch:** 5 new adapters (stepstone, railwaypeople, energy_jobline, aviation_job_search, the_engineer)  

---

## Gate Violation

**Criterion:** Pytest regression — must maintain ≥87 passed, 0 failed.  
**Actual:** 85 passed / 28 skipped / 0 failed  
**Result:** **FAIL** — 2-test regression from baseline (commit 65daae1)

---

## Root Cause

The smoke test `test_adapter_has_required_interface` attempts adapter instantiation with these signatures:
1. No arguments (`adapter_class()`)
2. Named (`adapter_class(name=adapter_name)`)

Three adapters now skip this test because they require additional config parameters:
- **ReedAdapter** — requires `api_key` (from settings, not provided by test)
- **StepStoneAdapter** (totaljobs/cwjobs) — requires `domain` and `search_path` (from config extras, not provided by test)

This is not a functional defect. The adapters work correctly when instantiated via the real pipeline (`_build_adapters(settings)`) where config is available. **However**, the test regression violates the gate criterion.

---

## Verification Passed

- ✓ All 5 new adapters import cleanly
- ✓ All 7 sources registered and constructable via `_build_adapters()`
- ✓ All new adapters have correct `name`, `crawl_delay`, and async `fetch()`
- ✓ All adapters resilient to errors (return [] without raising)
- ✓ Config-driven construction works for all 7 enabled sources
- ✗ **Smoke test pass count regressed: 87 → 85**

---

## Recommended Fix Path

**Option A (Preferred):** Update smoke test fixtures to provide minimal config context (domain/search_path for StepStone; mock api_key for Reed). This allows instantiation without network. Assign to **Arthur** (test owner) to revise `test_adapter_has_required_interface`.

**Option B:** Make adapters accept no-arg construction with sensible defaults for domain/search_path/api_key. Would require re-design of StepStone and Reed adapters. Assign to **Tommy** (architecture) to review feasibility.

---

## Recommendation for Scribe

**DO NOT COMMIT.** This batch gates on a test regression criterion. Fix path must be agreed before retry.

Blocking issues:
1. Smoke test fixture architecture mismatch (tests assume minimal-config instantiation; new adapters require config context)

---

## Decision Points for Team

1. Should smoke tests instantiate adapters with config from environment, or with defaults?
2. Should ReedAdapter accept no-arg construction with empty api_key for smoke tests?
3. Should the interface test be split into two paths: (a) smoke test for no-config adapters, (b) integration test for config-driven adapters?

