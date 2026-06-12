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
