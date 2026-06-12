# Routing

How work flows to team members. Squad (Coordinator) uses this table to decide who to dispatch.

## By Domain

| Signal / Keyword | Primary | Secondary |
|------------------|---------|-----------|
| Architecture, scope, tech-stack decisions, milestone planning | Tommy | — |
| New source adapter, scraping, HTTP/API client, scheduling, persistence | Michael | Tommy |
| Listing parser, field extraction (rate, IR35, dates, duration), LLM-assisted extraction, deduplication logic | Ada | Michael |
| Report format/layout, Markdown/HTML/email output, UK contract market vocabulary, IR35 nuance, day-rate norms | Polly | Tommy |
| Test cases, extraction accuracy, dedup quality, false-positive/negative checks, acceptance criteria | Arthur | — |

## Reviewer Roles

- **Arthur** — Tester / QA: gates extraction and dedup quality. May reject and require reassignment.
- **Tommy** — Lead: reviews architecture decisions and source-adapter contracts.

## Multi-Agent Triggers

| Trigger | Spawn |
|---------|-------|
| "Team, ..." | Tommy + 2-3 most relevant + Scribe |
| "Add source X" | Michael (adapter) + Ada (parser) + Arthur (test cases) in parallel |
| "Improve the report" | Polly (lead) + Ada (data shape changes if needed) |
| Any acceptance-criteria question | Arthur first, then domain agent |
