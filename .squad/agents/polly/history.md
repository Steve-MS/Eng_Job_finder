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

## 2026-06-12: Implementation Sprint 1 Complete — Cross-Team Sync
**Sprint outcome:** Tommy (architecture), Michael (scaffold + Reed), Ada (extraction + storage), Polly (reporter), Arthur (tests) all delivered. Architecture finalisation is binding. Full project scaffold + 28-field schema + 3-tier extraction + dedup + SQLite + Markdown reporter + 84-test suite complete. Orchestration logs: `.squad/orchestration-log/2026-06-12T{17:30,18:30}Z-{agent}.md`. Session log: `.squad/log/2026-06-12T1830-implementation-sprint-1.md`. **⚠️ Arthur surfaced 2 real defects for Ada's immediate attention:** (1) UAE/Dubai location filter rejects valid expat-PM listings (medium, ~2% impact), (2) "civil engineering" keyword false-fires mech-domain filter (high, ~8% false positives, affects dedup precision). Polly: continue refining sanity-check rules in Sprint 2; these defects may surface in report test cases. All acceptance criteria gates documented. Design locked; implementation ready to proceed to Sprint 2 (Michael's 6 remaining adapters).

## 2026-06-12: Reporter Module Built (Sprint Item #10)

### Region Taxonomy (8 canonical buckets)

Implemented in `src/mechpm/reporter/grouping.py`.  Regions in canonical display order:

| Region | Key cities / counties | Pay multiplier vs Midlands |
|--------|-----------------------|---------------------------|
| London | City of London, Canary Wharf | +20 % (1.20) |
| South-East | Surrey, Kent, Hampshire, Berkshire, Oxfordshire, Sussex, Reading, Portsmouth, Cambridge, Oxford | +10 % (1.10) |
| Midlands | Birmingham, Coventry, Derby, Nottingham, Leicester, Staffordshire, Lincoln, Warwick | baseline (1.00) |
| North | Manchester, Leeds, Liverpool, Newcastle, Sheffield, Bradford, Hull, York, Cheshire, Yorkshire | −10 % (0.90) |
| Scotland | Edinburgh, Glasgow, Aberdeen, Dundee, Inverness | −15 % (0.85) |
| Wales | Cardiff, Newport, Swansea, Wrexham | −10 % (0.90) |
| Remote | "remote", "wfh", "home-based", "nationwide" | neutral (1.00) |
| Other | Unmatched locations | neutral (1.00) |

**South-West decision:** No dedicated South-West bucket exists in the 8-region scheme.
Bristol, Bath, Swindon, Exeter, Gloucester, and other South-West cities are mapped to
"South-East" — the nearest broad southern region.  This is a pragmatic trade-off; if
Steve frequently sees South-West roles, consider adding a 9th bucket in a future sprint.

Location resolution uses substring matching on `location_normalized` (lower-cased).
Matching is done in keyword-list order so "Remote" beats any geo keyword, and "London"
beats South-East terms.

### Rate Bands Encoded (`src/mechpm/reporter/domain.py`)

```
RATE_BANDS_BY_SENIORITY = {
    "junior":    (350,  600),   # entry-level / coordinator
    "mid":       (480,  750),   # standard project manager
    "senior":    (700,  950),   # senior PM / delivery lead
    "programme": (900, 1500),   # programme director / portfolio
}
```

Seniority classification for pipeline sub-sections is based on `day_rate_max` (or
`day_rate_min` as fallback) compared against these raw thresholds — no regional
adjustment is applied for display bucketing (keeps UX simple).

The `rate_context()` function normalises the quoted rate by the region multiplier
before comparing against bands, so "(senior-band, Scotland)" means the rate is
senior-calibre *for Scotland*, not for London.

### Sanity-Rule Thresholds Refined

Updated from the earlier draft; now codified in `grouping.get_sanity_reasons()`:

| Rule | Threshold | Action |
|------|-----------|--------|
| Rate too low | `day_rate_max ≤ £250` | Flag: suspiciously low |
| Rate too high | `day_rate_max ≥ £1500` | Flag: unusually high — verify |
| Start date in past | `start_date < today` | Flag with days-overdue count |
| Duration extreme | `duration_weeks > 96` (> 24 months) | Flag: likely perm mis-labelled |
| Missing IR35 at high rate | `rate ≥ £700 AND ir35_status in (None, "undetermined")` | Flag: defer |
| Missing rate | both `day_rate_min` and `day_rate_max` are None | Flag: cannot benchmark |
| Vague location | `location_normalized` empty or whitespace | Flag: unable to assign region |
| Red-flag text | 9 patterns in description_clean (umbrella-only, perm, salary, etc.) | Flag with reason |

**Key clarification from task:** Sanity-flagged listings are NOT hidden — they appear
in a "⚠️ Review Queue" section at the bottom of the report with full reasons visible.
This overrides the earlier history note that said they are excluded until reviewed.

### Renderer Architecture Notes

- `render_weekly(listings, run_metadata, output_path)` — single public entry point
- Three card formats: full (New section), compact (Urgent), pipeline (region grid)
- Region sections use seniority sub-headings: Senior/Programme → Mid → Junior
- Each listing appears in exactly one region bucket (no cross-section bleed)
- Source URLs: multi-source deduped listings show all links, e.g. "[Reed](url) · [CWJobs](url)"
- Markdown escaping applied to all free-text fields (title, employer, description)
- Report output path: `reports/{YYYY-MM-DD}.md`; smoke-test path: `reports/smoke-test-YYYY-MM-DD.md`

---

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: implement report renderer. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.
