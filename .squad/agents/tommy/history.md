# Tommy — History

## Project Seed (2026-06-12)
- **Project:** Mech-PM-Finder — UK mechanical-engineering PM contract opportunity scanner
- **Owner:** Steve
- **Mission:** Regularly scan multiple UK job boards for contract project-management roles in mechanical engineering. Extract structured details (title, employer/agency, location, start date, duration, day rate, IR35 status, link). Deduplicate across sources. Produce a periodic report.
- **Team:** Tommy (Lead), Michael (Backend/Scraping), Ada (Data Extraction), Polly (Reporting & Domain), Arthur (Tester/QA), Scribe, Ralph.
- **Universe:** Peaky Blinders.
- **Tech stack:** TBD — Tommy to propose at first design step.

## Learnings
<!-- Append new learnings here. Use ISO 8601 dates. -->

### 2026-06-15: v0.2 Query Strategy — Multi-Source Keyword Slate

**Decision:** Broadened search from single narrow query to multi-query slate per source.

**Operator Support Matrix (confirmed from code + live testing):**

| Source | OR support | Exclude support | Multi-keyword strategy |
|--------|-----------|----------------|----------------------|
| Reed | No (single AND string) | No | Multiple sequential queries, union by jobId |
| Adzuna | Yes (`what_or`) | Yes (`what_exclude`) | Single broad OR query with exclusions |
| Energy Jobline | No (single keyword param) | No | Multiple sequential queries, union by node ID |
| RailwayPeople | N/A (whole site = filter) | N/A | No keyword query needed |
| Aviation Job Search | N/A (sitemap-driven) | N/A | Broaden URL pattern filter |

**Key design insights:**
1. Multi-query + union is the universal pattern for sources without OR support.
2. Source-level contract filters (Reed `contract=true`, Adzuna `contract=1`, EJL `contract_type=contract`) are first-pass noise reduction; Ada's `passes_contract` is the true gate.
3. Broader queries are safe when the downstream filter taxonomy is strong. PM_TITLE_RE + MECH_KEYWORDS + DISQUALIFY_PHRASES in series maintain ≥80% combined precision.
4. Energy Jobline's "0 stored" problem was caused by sector-specific PM titles (e.g., "Engineering Manager") not matching the narrow PM_TITLE_RE. Fix: expand PM_TITLE_RE + rely on mechanical filter as the precision backstop.
5. Cross-source dedup at Jaro-Winkler 0.85 is appropriate for v0.2; no tuning needed. Same-source dedup by `source_listing_id` handles multi-query overlap.
6. Adzuna's native `what_or` + `what_exclude` + `category` make it the most precise source at query time — less work for downstream filters.
7. Aviation Job Search: adding `/engineering/` category doubles URL coverage but needs `_MAX_JOBS` cap (50) to prevent 5-min crawl times.

**Expected yield improvement:** ~26 stored → ~55-95 stored (3-4× increase).

**Spec location:** `.squad/decisions/inbox/tommy-v02-query-slate.md`

### 2026-06-12: Reviewer-Lockout Patches (Tommy, escalation author)

**Context:** Arthur (Reviewer) rejected Ada's UK filter and mechanical-domain filter on 2 gold-set cases. Steve invoked strict Reviewer Rejection Lockout, assigning Tommy as revision author. Ada is locked out of this revision.

**Defect 1 — UAE/Dubai country detection (`regex_fields.py`)**
- Root cause: `_NON_UK_MAP` had no UAE/Dubai/Abu Dhabi entry; `detect_country()` defaulted to `"GB"`.
- Fix: Added `(re.compile(r"\b(dubai|abu\s+dhabi|uae|united\s+arab\s+emirates)\b", re.IGNORECASE), "AE")` as the first entry in `_NON_UK_MAP`. Also added USA (`US`), Saudi Arabia (`SA`), Qatar (`QA`), Singapore (`SG`), India (`IN`) per the small-list directive. Existing IE/NL/DE/FR entries retained.
- Pattern added: `\b(dubai|abu\s+dhabi|uae|united\s+arab\s+emirates)\b` → `"AE"`.

**Defect 2 — "civil engineering" false-fires mech disqualifier (`filters.py`)**
- Root cause: `passes_mechanical()` mechanical-sectors path used `any(phrase in title_lower for phrase in DISQUALIFY_PHRASES)` (substring match). `"civil engineer"` is a substring of `"civil engineering"`, so `edge_06_multi_discipline` (title: "Mechanical & Civil Engineering") was incorrectly rejected.
- Fix (mechanical-sectors path): Kept the substring disqualifier check, but added a mech-keyword counterbalance — when a disqualifier fires, the function now returns `True` only if a mechanical keyword from `MECH_KEYWORDS` also appears in the title (word-boundary anchored). This restores Ada's intended "mech_score beats disqualify_score" balance for multi-discipline titles.
  - Pattern logic: `has_disqualifier = any(phrase in title_lower …)` → if no disqualifier, pass; if disqualifier found, `return any(re.search(r"\b" + re.escape(kw) + r"\b", title_lower) for kw in MECH_KEYWORDS)`
- Fix (generalist path): Added `_DISQUALIFY_RES` pre-compiled list (`\b{phrase}\b` for each DISQUALIFY_PHRASES entry). Changed generalist scoring to use `_DISQUALIFY_RES` (word-boundary regex) instead of `ph in text` substring check.
  - Pattern for each phrase: `re.compile(r"\b" + re.escape(ph) + r"\b", re.IGNORECASE)`
  - This ensures `\bcivil engineer\b` does NOT match "civil engineering" in the generalist scoring path either.

**Pytest before/after:**
- Before: `3 failed, 84 passed, 26 skipped`
- After: `0 failed, 87 passed, 26 skipped`

**Scope note:** This patch is the minimum to clear Arthur's gate. Ada's broader country coverage (additional cities, disambiguation) and any further disqualifier coverage remain her responsibility for future sprints. Tommy's additions are conservative and scoped to the failing fixtures only.

### 2026-06-12: Architecture Finalisation — Key Decisions
- **Scheduler:** Windows Task Scheduler (native, zero-cost, supports missed-run catch-up and ad-hoc `schtasks /run`). No cloud infra for v0.1.
- **StepStone adapter family:** Single parameterised `StepStoneAdapter` class instantiated per-source via config.toml. Covers Totaljobs + CWJobs. Careerstructure deferred to v0.2.
- **Jobiqo adapters:** Two separate adapters (RailwayPeople = JSON from `__NEXT_DATA__`, Energy Jobline = Drupal HTML scrape). No shared base beyond the ABC.
- **Cloudflare (The Engineer Jobs):** httpx+headers first; Playwright approved as fallback for this source only.
- **Secret management:** `.env` (git-ignored) + `python-dotenv` in `src/mechpm/config.py`. No cloud secrets store.
- **Sector field:** Added to schema. 10-value enum (rail/aerospace/defence/energy/nuclear/construction/maritime/automotive/process/generalist). Populated by source-default + title-keyword map.
- **Crawl-delay:** Per-source registry in config.toml. Sources run sequentially. RailwayPeople + Energy Jobline = 10s; others 3–5s.
- **Playwright:** Conditionally approved, scoped to The Engineer Jobs only. Optional dependency (`mechpm[browser]`).

### 2026-06-12: MVP Architecture Decided
- **Tech stack:** Python 3.12+ / SQLite / OpenAI GPT-4o-mini / Markdown reports / httpx for async HTTP.
- **Why Python:** Best scraping + LLM ecosystem, runs natively on Steve's Windows machine, easy for all agents to extend.
- **Why SQLite:** Zero-ops, single-file DB, portable, supports dedup queries. No cloud storage needed for MVP.
- **Why GPT-4o-mini:** Cheapest reliable model for structured extraction (~$0.15/1M input tokens). Upgrade path to local Ollama for offline.
- **Scheduler:** Manual CLI for v0.1; Windows Task Scheduler in v0.2. No cloud infra.
- **Source contract:** Python ABC with async `fetch() -> list[RawListing]`. Adapters must never crash the pipeline.
- **MVP sources:** Reed.co.uk (API) + CWJobs (scrape) — proves both acquisition paths.
- **Seven extraction fields:** title, company, location, day_rate, duration, ir35_status, start_date.
- **Convention:** All adapters live in `src/mechpm/adapters/`, one file per source.
- **Convention:** Reports output to `reports/YYYY-MM-DD.md`.
- **Convention:** Config in `config.toml` at repo root.

---

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: implement to spec. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.

---

## 2026-06-12: Implementation Sprint 1 Complete — Cross-Team Sync
**Sprint outcome:** Tommy (architecture), Michael (scaffold + Reed), Ada (extraction + storage), Polly (reporter), Arthur (tests) all delivered. Architecture finalisation is binding. Full project scaffold + 28-field schema + 3-tier extraction + deduplicate + SQLite + Markdown reporter + 84-test suite complete. Orchestration logs: `.squad/orchestration-log/2026-06-12T{17:30,18:30}Z-{agent}.md`. Session log: `.squad/log/2026-06-12T1830-implementation-sprint-1.md`. **⚠️ Arthur surfaced 2 real defects for Ada's immediate attention:** (1) UAE/Dubai location filter rejects valid expat-PM listings (medium, ~2% impact), (2) "civil engineering" keyword false-fires mech-domain filter (high, ~8% false positives, affects dedup precision). All acceptance criteria gates documented. Design locked; implementation ready to proceed to Sprint 2 (Michael's 6 remaining adapters).

---

## Architectural decisions

### 2026-06-14: Pipeline Wiring — End-to-End CLI Spec

**Problem:** `run_all()` only persists raw JSONL. None of the downstream modules (extraction, filtering, dedup, storage, reporting) are invoked. No SQLite DB or Markdown report is produced.

**Decisions (summary):**
1. **CLI shape:** Single `run-all` command does fetch→process→report end-to-end. Flags (`--skip-fetch`, `--skip-report`, `--since`, `--source`) provide composability without subcommand proliferation.
2. **Handoff:** Re-read from JSONL on disk (decoupled; crash-resumable).
3. **SQLite:** `data/mechpm.sqlite`, idempotent schema creation, UPSERT by `listing_id`. Added `first_seen_at` + `times_seen` columns for trend analytics.
4. **Report scope:** Default last-7-days with 🆕 flag for today; override via `--since`.
5. **Report path:** `reports/{YYYY-MM-DD}.md` (overwrite same-day re-runs).
6. **Failure isolation:** Skip + quarantine per listing (WARNING log + `data/quarantine/`).
7. **Idempotency:** `ON CONFLICT(listing_id) DO UPDATE SET last_seen_at=..., times_seen=times_seen+1`.
8. **Empty source:** `run_manifest.json` emitted per run; reporter reads it for source-status table.

**Spec:** `.squad/decisions/inbox/tommy-pipeline-wiring-spec.md`
**Implementor:** Michael
**Work items:** 10 (manifest, schema migration, pipeline module, quarantine, CLI wiring, flags, reporter extension, skip-report, e2e test, run.bat update)

### 2026-06-15: JSONL File Policy & rapidfuzz Dependency

**Problem:** Orchestrator's `_persist_jsonl` opened JSONL in `"a"` (append) mode. Three calibration runs today inflated `railwaypeople.jsonl` to ~150 rows for 50 actual jobs. Content-hash `listing_id`s diverged across runs (pre/post-calibration parse differences), defeating identity dedup and producing ghost "Unknown Employer" rows in the live report.

**Decision 1 — JSONL policy: Option (A) Truncate per fetch.**
Open `_persist_jsonl` in `"w"` mode. Each fetch run is the sole source of truth for that source+date. Loss window on mid-run crash is acceptable; SQLite holds all processed history and the operator simply reruns.

**Decision 2 — rapidfuzz: Option (iii) Promote to required core dep; openai stays optional.**
rapidfuzz is algorithmic baseline — identity-only dedup lets ghost duplicates survive. Pure-C wheel, no key/cost. openai stays optional (cost + key required).

**Spec:** `.squad/decisions/inbox/tommy-jsonl-policy-and-deps.md`
**Implementor:** Michael
**Work items:** 5 (orchestrator open-mode fix, pyproject.toml, delete 2 stale JSONLs, overwrite regression test, rapidfuzz smoke test)

## 2026-06-15: v0.2 sprint complete

v0.2 query-slate expansion sprint concluded. Multi-session continuation across 8 agent spawns:
- Tommy (Lead): Query-slate specification (700+ line decision drop)
- Michael-16: Adzuna API adapter (5 listings)
- Michael-17: M1-M7 multi-query refactor (orchestrator dedup)
- Ada-4: A1-A4 filter taxonomy expansion (energy sector titles + country detection)
- Ada: EJL root-cause fix (101→3 stored via filters)
- Ada: Site Manager precision (false-positive rejection)
- Ada: rate_parser module (18 listings structured)
- Polly: Rate-period-aware rendering (seniority bands + normalization)

Final state: **44 stored across 5 sources, 18 with structured day-rate, 398 tests passing.**

### 2026-06-15: Deployment — GitHub Action + Pages (Zero-Install for PMs)

**Problem:** Non-technical PMs can't install Python locally. The agent needed a zero-install deployment path that produces a bookmarkable URL refreshed weekly.

**Solution:** GitHub Actions weekly cron + GitHub Pages.

**Key architectural decisions:**
1. **Cron:** `0 16 * * 5` (Friday 17:00 BST = 16:00 UTC)
2. **DB persistence:** Commit `data/mechpm.sqlite` back to repo after each run (Option A — simplest, fork-friendly, no eviction risk unlike Actions cache)
3. **Pages:** Modern `actions/deploy-pages@v4` workflow-based deploy (no `gh-pages` branch)
4. **Archive:** `reports/index.html` (listing page) + `reports/latest.html` (stable redirect to newest)
5. **Secrets:** `REED_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` — 3 secrets the user must set
6. **Failure isolation:** `continue-on-error: true` on pipeline step; partial reports still publish
7. **Manual trigger:** `workflow_dispatch:` always available

**Deliverables:**
- `.github/workflows/weekly.yml` — full CI/CD pipeline (checkout → Python → install → pytest gate → run-all → index → commit → Pages deploy)
- `src/mechpm/reporter/index_render.py` — generates archive index + latest redirect
- `tests/test_index_render.py` — 13 tests (discovery, ordering, links, redirect, edge cases)
- `.env.example` — restored with current env var list
- `README.md` — complete rewrite: hosted-first "5 steps to bookmark your report" + local fallback

**Test state:** 425 passed, 25 skipped, 0 failed.

**User action required:** Steve must add 3 repo secrets + enable Pages (source: GitHub Actions) + trigger first run manually.
