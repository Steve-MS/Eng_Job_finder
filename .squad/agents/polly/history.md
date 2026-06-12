# Polly — History

## Project Seed (2026-06-12)
- **Project:** Mech-PM-Finder
- **Owner:** Steve
- **My role:** Reporting & Domain. I own report format, grouping/highlighting, and UK contract-market domain knowledge.
- **Mission:** Produce periodic reports that surface the most relevant UK mechanical-engineering PM contract roles with start dates, duration, and day rate.
- **Domain notes seed:** UK mechanical-engineering PM day rates typically span ~£450-£750/day (inside IR35 often lower); senior/programme roles can reach £900+. IR35 status (inside / outside / undetermined) materially affects take-home and must be surfaced prominently.

## Learnings

### 2026-06-12: Report Format & Domain Notes (MVP Spec)

#### Report Grouping Decision
**Rationale:** MVP report groups content by signal importance for Steve's workflow:
1. **New roles** (discovery; first 5 min scan)
2. **Urgent starts ≤14 days** (action; next 5 min if interested)
3. **Full pipeline by region+seniority** (backlog/strategic browsing)

Within each group, sort by start date (ascending) to surface imminent opportunities first.

**Output:** Markdown in `reports/{YYYY-MM-DD}.md`, published weekly Friday 17:00. Later: email digest, Slack, RSS.

#### Highlighting Rules
- **🆕 New:** First appearance in dedupe system (discovered this run)
- **⚡ Urgent:** Start date ≤14 calendar days from report date
- **💰 Premium:** Day rate ≥£700 outside IR35 (top-quartile signal)
- **⚠️ Sanity flag:** Triggered in processing; role does not appear in report until reviewed and corrected/approved

#### UK Market Domain Notes Summary
- **Day rate bands by seniority:** Junior £350–600/day | Mid £480–750/day | Senior £700–950/day | Programme £900–1500+/day
- **IR35 impact:** Outside IR35 ~25% higher net but full tax burden; inside IR35 quasi-employee, lower pay but agency handles tax. Inside more common at junior level; outside standard at senior (£700+).
- **Regional multipliers:** London +15–25% vs Midlands baseline; South-West +5–10%; North −5–15%; Scotland −10–20%
- **Red flags:** Rate ≤£250 or ≥£1500 (unusual); no IR35 status at high rate; start date in past; umbrella-only with gross rate quoted; perm role mis-labelled as contract
- **Agencies:** Gatenby Sanderson, Kforce, Apex, Heidrick & Struggles (top tier); Morgan Hunt, Harvey Nash (broad); verify unknowns on Trustpilot
- **Seniority mis-labels:** "Coordinator" with P&L scope is actually mid/senior; "Senior PM" with site-based + 6mo is actually coordinator

#### Sanity-Check Rules (Processing Gate)
Listings triggering ⚠️ do not reach report; review queue:
- Rate ≤£250 or ≥£1500: verify or reject
- Start date in past: reject (stale)
- Duration >24mo: re-classify (likely perm)
- No IR35 at rate ≥£700: defer; ask for clarification
- Missing rate or vague location: defer; require specifics
- Title ≠ description seniority: re-classify

---

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: implement report renderer. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.
