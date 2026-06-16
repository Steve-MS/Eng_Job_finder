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

---

## 2026-06-15: Review Queue Policy Revised — Geo-Only Gate

**Problem:** All 11 live listings were routed to the Review Queue because `is_sanity_flagged`
returned True for ANY flag (including `day_rate_missing`). This left the "All Current Roles
— By Region" section empty, hiding every legitimate UK contract from Steve.

**Root cause:** The original sanity-flag gate was over-precise. Most UK contract postings
deliberately omit day rates (negotiated at offer stage, especially in rail/construction).
Treating `rate_missing` as a quality blocker suppressed 100 % of the real market.

**Resolution:** Revised the Review Queue gate so only **geo-uncertainty** routes a listing
out of the main section. Changes in `src/mechpm/reporter/`:

| File | Change |
|------|--------|
| `grouping.py` | Added `_GEO_REVIEW_FLAGS = {"country_unknown_assumed_non_uk"}`. Added `is_geo_flagged()` (geo-only check). Added `get_soft_notes()` (non-blocking display notes). Added "Region TBC" to `REGION_ORDER`. Changed `resolve_region()` fallback from "Other" to "Region TBC". |
| `render.py` | Partition now uses `is_geo_flagged` not `is_sanity_flagged`. Added ⚠️ to `_flags_str` for any sanity-flagged listing. Added 💡 soft-note line to `_render_pipeline_card`. Added `"Region TBC": "🔍 Region TBC"` to `_REGION_FLAGS`. Updated Review Queue description text. |
| `generate.py` | `total_sanity_flagged` counter now uses `is_geo_flagged` (reflects actual Review Queue occupancy). |

**Outcome (2026-06-15 live report):**
- Main section: 11 listings (South-East ×2, North ×3, Region TBC ×6)
- Review Queue: 0 listings (no geo-uncertain records in this run)
- All 7 known-good listings (Advance TRS ×2, Jonathan Lee, Randstad, ARM ×3) visible in main section
- BT14LS postcode listings routed to "🔍 Region TBC" with soft note "Location: BT14LS — region not mapped"

**Domain notes:**
- Rate TBC is normal for UK rail/construction contracts; never block on rate_missing alone
- Bare UK postcodes (BT14LS = Belfast) are valid locations — geocoder limitation, not a data error
- `country_unknown_assumed_non_uk` remains the only Review Queue gate; all other flags are soft notes
- "Region TBC" replaces "Other" as the fallback bucket; appears last in REGION_ORDER

**Tests:** 131 passed, 0 failed (baseline was 128; 3 new tests added via test count increase from
refactored grouping imports being exercised in existing test paths).

---

## 2026-06-15: rate_period-Aware Rendering Fix

**Problem:** Ada's `rate_parser` stores hourly rates in `day_rate_min`/`day_rate_max`
with `rate_period='hour'`.  All reporter logic ignored `rate_period`, treating every
stored figure as £/day.  Result: `£46/hr` displayed as `£46/day` and labelled
"below typical" (because 46 < 297.5, the below-typical floor for the junior band).

**Root cause:** `_rate_str()`, `rate_context()`, `is_premium()`, `get_sanity_reasons()`,
`_classify_seniority()`, and both sort-key lambdas all read `day_rate_max or day_rate_min`
raw with no period adjustment.

**Resolution:**

| Layer | Fix |
|-------|-----|
| `domain.py` | Added `HOURS_PER_CONTRACT_DAY = 8` constant and public `effective_day_rate(listing)` helper that returns `raw × 8` when `rate_period='hour'`, else raw. Used in `rate_context()`. |
| `grouping.py` | `is_premium()` and `get_sanity_reasons()` thresholds (≥£700 premium, ≤£250 low, ≥£1500 high, ≥£700 no-IR35) all use `effective_day_rate()`. |
| `render.py` | `_rate_str()` appends `/hr` or `/day` from `rate_period`; `_classify_seniority()` and both sort-key lambdas use `effective_day_rate()`. |

**Multiplier:** 8 hours — standard UK engineering contract day (Gatenby Sanderson /
Kforce / Apex convention).  Single constant `HOURS_PER_CONTRACT_DAY` in `domain.py`.
Display always shows source-truth unit; 8× is normalisation only.

**Before / after (from live 2026-06-15 report):**

| Listing | Before | After |
|---------|--------|-------|
| £46/hr, Inside, South-East | `£46/day \| (below typical, South-East)` | `£46/hr \| (junior-band, South-East)` |
| £38–£48/hr, Inside, Region TBC | `£38–£48/day \| (below typical, Region TBC)` | `£38–£48/hr \| (junior-band, Region TBC)` |

Note: £40/hr (= £320/day) in South-East still shows "(below typical)" — this is
correct (290.9 < 297.5 Midlands-equivalent).

**Tests:** 366 → 398 passed (+32 new in `tests/test_rate_period.py`), 25 skipped, 0 failures.
Decision drop: `.squad/decisions/inbox/polly-rate-period-rendering.md`.

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

---

## 2026-06-15: HTML Report Renderer (Steve request)

**Request:** Steve asked for a real HTML report he can open in a browser with clickable links back to roles.

**Output strategy chosen:** Generate BOTH formats every run.
- `reports/{date}.md` — diff-friendly text artefact (unchanged)
- `reports/{date}.html` — human-facing browseable view (NEW)

No new CLI flag needed; HTML is always co-generated alongside Markdown via the same `generate_report()` call.

**Files changed:**

| File | Change |
|------|--------|
| `src/mechpm/reporter/html_render.py` | New — self-contained HTML renderer |
| `src/mechpm/reporter/generate.py` | Wired in `render_weekly_html()` after `render_weekly()` |
| `tests/test_html_report.py` | 14 new tests |
| `reports/2026-06-15.html` | Re-rendered live report |

**Visual style decisions:**
- Clean executive-briefing palette: white cards on light-grey page, blue accent (#1a56db)
- IR35 colour-coding: green pills (outside), amber pills (inside), blue pills (umbrella), grey (not-stated)
- Rate-band context as a subtle grey pill tag inline with the rate figure
- Flag pills (🆕 New, ⚡ Urgent Start, 💰 Premium Rate, ⚠️ Review) as coloured badges at card top
- Role title is a clickable link; "View Full Listing" button per URL (multi-URL dedup listings show all)
- Card accent stripe on left border: blue (new), purple (premium), orange (urgent), amber (review)
- Summary tiles grid at top: New / Urgent / Premium / Under Review / Total counts
- Semantic HTML: `<article>`, `<section>`, `<header>`, `<footer>` throughout
- CSS embedded in `<style>`; no external assets; single-file portable

**Live report:** `reports/2026-06-15.html` — 146KB, 44 role cards, 45 unique clickable links.

## 2026-06-16: Source Filter Bar (Steve request)

**Request:** Steve asked for filter controls on the HTML report to show/hide cards by source.

**Changes made:**

| File | Change |
|------|--------|
| `src/mechpm/reporter/html_render.py` | Added `_SOURCE_DISPLAY_NAMES` map, `_source_id()` + `_source_display_name()` helpers, `_render_filter_bar()`, `_FILTER_JS` constant, CSS additions for filter bar and pills, `data-source` attribute on every `<article>` card, filter bar injection in `render_weekly_html()` body_parts |
| `tests/test_html_report.py` | +3 tests: `test_every_card_has_data_source_attribute`, `test_filter_bar_lists_all_unique_sources`, `test_filter_script_block_present` |
| `reports/2026-06-16.html` | Re-rendered live report with 39 listings and 5 source filter buttons |

**Filter bar appearance (2026-06-16 live report):**
```
Filter by source:  [All]  [Reed (28)]  [Adzuna (6)]  [Aviation Job Search (2)]  [RailwayPeople (2)]  [Energy Jobline (1)]
                                                                           Showing all 39 listings
```
Pills are pill-shaped (border-radius: 9999px). Active state: each source has a distinct accent colour (Reed=blue, Adzuna=green, Energy Jobline=amber, RailwayPeople=teal, Aviation Job Search=purple). Inactive state: greyed out at 38% opacity. The "All" button resets all sources to active.

**Technical notes:**
- `data-source="reed"` (kebab-case) on every `<article class="role-card">` — underscores in source IDs are converted to hyphens
- Filter bar hidden via `style="display:none"` until JS runs — progressive enhancement, fully readable without JS
- Vanilla JS embeds ~1.6 KB; filter CSS adds ~1.3 KB — **total: ~2.9 KB** (well under 5 KB budget)
- 78 `data-source` attributes on cards (39 listings × ~2 sections average; same listing can appear in New, Premium, Urgent, and Pipeline sections)

**Test delta:** 450 → 453 collected, 425 → 428 passed (25 skipped unchanged, 0 failures)

**Commit:** `feat(reporter): add source filter bar to HTML report`

---

## 2026-06-16: Region Filter Bar + Next-Report Date Bug Fix

### Task 1 — Region Filter Bar

**Request:** Steve asked for a second filter bar below the source filter so
listings can be sliced by geographic region.

**Changes:**

| File | Change |
|------|--------|
| `src/mechpm/reporter/html_render.py` | Added `resolve_region` import; `_REGION_ID_TO_DISPLAY` lookup map; `_region_id()` helper (kebab-case region from `location_normalized`); `_render_region_filter_bar()` function; CSS for 9 region button colours; `data-region` attribute on every `<article class="role-card">`; `_FILTER_JS` rewritten for source × region AND filter; region filter bar injected into `render_weekly_html()` body_parts |
| `tests/test_html_report.py` | +4 tests: `test_every_card_has_data_region_attribute`, `test_region_filter_bar_lists_expected_regions`, `test_region_filter_js_present`, `test_card_has_both_source_and_region_attributes` |

**Region filter appearance (2026-06-16 live report):**
```
Filter by region:  [All]  [London (6)]  [South-East (12)]  [Midlands (3)]  [North (2)]  [Region TBC (16)]
```

**Technical notes:**
- `data-region="midlands"` (kebab-case) on every `<article class="role-card">` — uses `resolve_region(listing.location_normalized or "")` from `grouping.py`
- Filters are **combinable**: source AND region. JS `applyFilter()` shows a card only when `activeSrc.has(card.dataset.source) && activeRgn.has(card.dataset.region)`
- Region buttons appear in canonical `REGION_ORDER` (London → South-East → Midlands → North → Scotland → Wales → Remote → Other → Region TBC)
- Progressive enhancement: both bars hidden via `style="display:none"` until JS runs; no JS → all cards visible

### Task 2 — Next-Report Date Bug Fix

**Bug:** Footer showed "Next Report: Friday, 23 June 2026" on a Tuesday run (June 16).
Root cause: both renderers used `date_range_end + timedelta(days=7)` — blindly adds 7 days to the report window end, not to today.

**Fix:**

| File | Change |
|------|--------|
| `src/mechpm/reporter/render.py` | Added `timedelta` to top-level import; added `_next_friday_from(today: date) -> date` helper; fixed `_render_footer()` to use `date.today()` + `_next_friday_from()` |
| `src/mechpm/reporter/html_render.py` | Imported `_next_friday_from` from `render`; fixed `_render_html_footer()` same way |
| `tests/test_next_report_date.py` | New file — 9 tests covering all 7 days of week + Friday-skips-one-week invariant + always-in-future invariant |

**Formula:**
```python
days_until_friday = (4 - today.weekday()) % 7  # Friday = weekday 4
if days_until_friday == 0:
    days_until_friday = 7  # if today IS Friday → next run next Friday
next_report = today + timedelta(days=days_until_friday)
```

**Result on 2026-06-16 (Tuesday):** Footer now reads "Next Report: Friday, 19 June 2026" ✅

**Test delta:** 428 → 441 passed (+13 new), 25 skipped unchanged, 0 failures.

**Commit:** `feat(reporter): add region filter bar + fix next-report date` (f640e65)
