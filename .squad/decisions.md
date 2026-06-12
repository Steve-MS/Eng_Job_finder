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


