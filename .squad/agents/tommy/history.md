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
