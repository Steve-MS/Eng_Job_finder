# Ada — History

## Project Seed (2026-06-12)
- **Project:** Mech-PM-Finder
- **Owner:** Steve
- **My role:** Data Extraction. I own the canonical listing schema, field extractors, and dedup.
- **Mission:** Turn raw scraped listings into clean structured records covering title, employer, location, start date, duration, day rate, IR35 status.
- **Filtering rules:** Contract only (exclude permanent). UK only. Project-management roles. Mechanical-engineering domain.

## Learnings

### 2026-06-12 — Schema, Filters, and Dedup Design

#### Schema Field List (canonical)
`listing_id`, `source`, `source_url`, `source_listing_id`, `title`, `employer`, `agency`,
`location`, `location_normalized`, `country`, `posted_at`, `start_date_raw`, `start_date`,
`asap_flag`, `duration_raw`, `duration_weeks`, `day_rate_min`, `day_rate_max`, `rate_currency`,
`rate_period`, `ir35_status`, `contract_type`, `remote_policy`, `description_raw`,
`description_clean`, `source_urls`, `discovered_at`, `last_seen_at`

**Key design choices:**
- `listing_id` = SHA-256(source + ":" + source_listing_id) → first 22 base64url chars. Deterministic, stable.
- Separate `start_date_raw` (verbatim) from `start_date` (parsed ISO date) — preserves original text for audit.
- `source_urls` is an array (populated by dedup pipeline) so one canonical record carries all board links.
- `duration_weeks` derived by normalising months × 4, years × 52 from `duration_raw`.
- `ir35_status` enum: `inside / outside / undetermined / not_stated`. Default `not_stated`.
- `contract_type` enum: `contract / perm`. Fixed-term contracts → `contract`. Contract-to-perm → `perm`.

**Extraction tiers:**
- Structured (from API/spider): source, source_url, source_listing_id, title, employer, agency, location, posted_at, description_raw
- Regex: listing_id, location_normalized, country, start_date_raw, asap_flag, duration_raw, duration_weeks, day_rate_min/max, rate_currency, rate_period, ir35_status, contract_type, remote_policy
- LLM-assisted (last resort only): rate prose, start date prose, fuzzy IR35 prose, location ambiguity

#### Filter Rules Summary
1. **Contract type**: `contract_type == "contract"`. FTC passes; contract-to-perm fails.
2. **Geo**: `country == "GB"`. Default GB for UK-specific boards. Dublin/Europe → reject.
3. **Role (PM)**: title matches PM regex OR ≥ 2 body signals (Gantt, risk register, milestone, etc.).
4. **Domain (Mechanical Eng)**: `mech_score >= 1` AND `mech_score > disqualify_score`. Disqualifying domains: civil-only, electrical-only, software/IT-only.

#### Dedup Strategy
- **Block** by (location_normalized × duration_weeks bucket) to limit pairwise work.
- **Match** on: title Jaro-Winkler ≥ 0.85 + employer/agency match + location match + rate overlap ≥ 50% + posted_at within 14 days.
- **Canonical** = most fields populated; source priority Reed > CV-Library > CWJobs > LinkedIn > Indeed (TBC with Michael).
- **Merge**: `source_urls` array; widest rate range; stated IR35 over `not_stated`; latest `last_seen_at`.
- Re-posts after >14 days from same source → treated as new listing, not a duplicate.

#### Decision file
Written to `.squad/decisions/inbox/ada-schema-and-filters.md` — awaiting Arthur sign-off.

---

## 2026-06-12: Implementation Sprint 1 Complete — Cross-Team Sync
**Sprint outcome:** Tommy (architecture), Michael (scaffold + Reed), Ada (extraction + storage), Polly (reporter), Arthur (tests) all delivered. Architecture finalisation is binding. Full project scaffold + 28-field schema + 3-tier extraction + dedup + SQLite + Markdown reporter + 84-test suite complete. Orchestration logs: `.squad/orchestration-log/2026-06-12T{17:30,18:30}Z-{agent}.md`. Session log: `.squad/log/2026-06-12T1830-implementation-sprint-1.md`. **⚠️ Arthur surfaced 2 real defects for Ada's immediate attention:** (1) UAE/Dubai location filter rejects valid expat-PM listings (medium, ~2% impact), (2) "civil engineering" keyword false-fires mech-domain filter (high, ~8% false positives, affects dedup precision). Both defects documented in orchestration log + session log. Ada: priority updates to location_normalized regex and mech_keywords list. All acceptance criteria gates documented. Design locked; implementation ready to proceed to Sprint 2 (Michael's 6 remaining adapters).

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: implement LLM extractor + dedup harness per Tommy's adapter contract. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.

---

## 2026-06-12: Sprint Items #8 and #9 — Implementation Complete

### Files delivered

| File | Purpose |
|------|---------|
| `src/mechpm/models.py` | Extended Polly's stub with full 29-field schema + auto listing_id |
| `src/mechpm/extractor/__init__.py` | Package export: `extract` |
| `src/mechpm/extractor/pipeline.py` | 3-tier orchestrator |
| `src/mechpm/extractor/structured.py` | Tier-1: direct field lift from RawListing |
| `src/mechpm/extractor/regex_fields.py` | Tier-2: all regex patterns with inline examples |
| `src/mechpm/extractor/llm_fallback.py` | Tier-3: gpt-4o-mini, mock-injectable |
| `src/mechpm/extractor/sector.py` | SOURCE_DEFAULTS + KEYWORD_MAP + assign_sector() |
| `src/mechpm/extractor/filters.py` | 4 filters + passes_all() |
| `src/mechpm/extractor/dedup.py` | dedupe() + dedupe_with_groups() |
| `src/mechpm/storage/__init__.py` | Package export: `Repo` |
| `src/mechpm/storage/sqlite.py` | Repo class + DDL + smoke test |
| `.squad/decisions/inbox/ada-dep-additions.md` | Dep flag for Michael |

### Learnings

#### Fields that needed LLM fallback after writing regexes
After writing the Tier-2 regexes, the following fields still have material
failure rates on real-world job description text and justify the LLM fallback:

1. **Rate from prose** — Some listings bury the rate in the body with phrasing
   like "competitive day rate around £500" or "rate negotiable circa £600pd" which
   RATE_RANGE_RE / RATE_SINGLE_RE miss.  LLM fallback catches free-form rate prose.

2. **Start date from prose** — "Looking for an immediate start or mid-July"
   and "availability in Q3 2026" are not matched by START_DATE_RE.  LLM fallback
   disambiguates relative and quarter-based references.

3. **IR35 from body** — The regex catches explicit "Outside/Inside IR35" but misses
   implied phrasing like "paid via PSC", "umbrella preferred", or "direct engagement
   via Ltd company".  LLM fallback with low confidence (0.65) is used here; the
   field stays `None` if LLM confidence < 0.6.

4. **Location disambiguation** — Edge cases like "Hybrid — predominantly Didcot"
   or "Commutable from Birmingham area" are not normalised by the postcode-strip regex.
   LLM fallback resolves these to canonical city names.

#### Sector keyword map summary
- 9 sector buckets with explicit keyword lists (rail 14 kws, aerospace 11,
  defence 11, energy 16, nuclear 10, construction 12, maritime 11, automotive 12, process 11).
- SOURCE_DEFAULTS covers the 3 vertical-specific boards
  (railwaypeople→rail, energy_jobline→energy, aviation_job_search→aerospace).
- For generalist boards, keyword scoring uses title × 2 + description × 1 weighting.
- "Generalist" sector is the catch-all default (no keyword match AND no source default).
- Overlap noted: "bae systems" appears in both aerospace and defence — tie broken
  by whichever sector scores higher overall; in practice co-occurring keywords
  (submarine + bae → defence; airframe + bae → aerospace) resolve it.

#### Dedup similarity threshold
- Jaro-Winkler threshold: **0.85** (as agreed in decisions.md).
- Chosen over WRatio because JW rewards common prefix matching which is well-suited
  to job title variations like "Senior PM – Mechanical" vs "Sr. PM - Mechanical Eng".
- Rate-band overlap minimum: **50%** — loose enough to catch same role re-posted with
  slight rate tweak; tight enough to avoid false merges across very different rates.
- 14-day posting window: re-posts after >14 days treated as new listings (market
  norm for short contract roles where the same role is reposted weekly).
- rapidfuzz not in pyproject.toml yet → decision drop filed at
  `.squad/decisions/inbox/ada-dep-additions.md`; dedup degrades gracefully to
  identity (no merge) when package is absent.

#### Polly compatibility notes
- Kept `location: str = ""` (Polly display field) and `location_normalized: str = ""`
  (non-Optional, Polly calls `.strip()` on it).  Ada's pipeline sets both.
- Kept `ir35_status: Optional[str]` with `None` as the "not_stated" sentinel so
  Polly's `in (None, "undetermined")` sanity check fires correctly.
- Added `sector`, `source_url`, `source_listing_id`, `country`, `posted_at`,
  `start_date_raw`, `asap_flag`, `duration_raw`, `rate_currency`, `rate_period`,
  `description_raw` as new fields — all additive, zero reporter breakage.
- `listing_id` is now auto-computed by `model_validator` if not supplied.
- `datetime.utcnow()` (deprecated in 3.12) replaced with `datetime.now(timezone.utc)`.

---

## 2026-06-12: Tommy Patched Defects Under Lockout

Tommy patched Ada's two filter defects (UAE country detection + civil/mech disambiguation) under strict reviewer-lockout after Arthur's rejection. Both defects fixed and validated. pytest: 3 failed → 0 failed. Ada: when lockout lifts, broader country/disqualifier coverage is yours.

---

## 2026-06-14: UK Filter Strengthening — Geo Detection from Title/Description

### Problem
A listing titled *"Project Manager (High Speed Rail) - MENA Region"* (from RailwayPeople) leaked into the main report because:
1. `location_raw` was empty (adapter calibration gap — separate work).
2. `detect_country` only scanned `location`; "MENA" in the title was invisible to it.
3. `passes_uk` defaulted to **allow** when country was unknown (defaulted to "GB").

### Changes delivered

#### `regex_fields.py`
- Added `(mena|middle\s+east)` → `"AE"` as the first entry in `_NON_UK_MAP` (most general, evaluated before Dubai/UAE specific).
- Extended `detect_country(location_raw, title=None, description=None)` with two optional parameters. When `location_raw` is absent/empty, the function now scans `title` then `description` in order; first non-UK match wins. Returns `"GB"` as sentinel when no signal is found (preserves `country: str` type contract with `NormalizedListing`).
- The pipeline call `detect_country(location_raw_value)` (no title/description) is backward-compatible and unchanged.

#### `filters.py`
- Imported `_NON_UK_MAP` at module level.
- Rewrote `passes_uk`: explicit non-UK country → reject; location present + GB → pass; location empty (country defaulted to GB) → secondary scan of `title` then `description_raw` using `_NON_UK_MAP`; non-UK signal found → reject; no signal found → reject **and** append `"country_unknown_assumed_non_uk"` to `listing.sanity_flags` for Review Queue routing.
- Precision improvement: the "default-allow on unknown country" gap is closed.

### Gold-set additions (5 new fixtures)
| File | Category | Fail filter | Signal location |
|------|----------|-------------|-----------------|
| `neg_09_nonuk_mena_in_title` | negative | uk_filter | "MENA" in title |
| `neg_10_nonuk_dubai_in_desc` | negative | uk_filter | "Dubai" in description |
| `neg_11_nonuk_germany_in_title` | negative | uk_filter | "Frankfurt" in title |
| `pos_09_uk_clear_signal` | positive | — (pass) | "Manchester, UK" in location |
| `edge_07_unknown_country_no_signal` | edge_case | uk_filter | no geo signal anywhere |

### Test results
- Baseline: 106 passed → Post-change: 123 passed (+17; requirement was ≥ 5 new).
- 6 new dedicated unit tests for `passes_uk` and `detect_country` behaviour.
- 0 failures.

### Live verification
Re-ran `mechpm.cli run-all --skip-fetch` against existing data. "MENA Region" listing now appears in **Review Queue** only; absent from all main report sections.

### Design notes
- The `country` field on `NormalizedListing` stays `str = "GB"` — no model change needed. The filter is the right place to interpret "empty location + GB default" as unknown.
- Sanity flag `"country_unknown_assumed_non_uk"` hooks into the existing `sanity_flags: list[str]` field on the model (Polly-owned infrastructure). No new schema changes.
- The `detect_country` optional params are available for future pipeline updates when title/description are passed at extraction time; the filter's secondary scan provides coverage in the meantime.
- Decision logged at `.squad/decisions/inbox/ada-uk-filter-strengthening.md`.

---

## 2026-06-15: v0.2 Filter Taxonomy Expansion (A1–A4)

### Work delivered
Implemented all four of Tommy's work items per `.squad/decisions/inbox/tommy-v02-query-slate.md` Section 2.

#### A1 — PM_TITLE_RE expansion
Added 10 new alternations: `engineering manager`, `construction manager`, `site manager`,
`commissioning manager`, `m&e manager`, `installation(s) manager`, `contract(s) manager`,
`project engineer`, `planning manager`, `package manager`.

#### A2 — MECH_KEYWORDS expansion
Merged Tommy's 57-term `MECH_KEYWORDS_ADDITIONS` into the existing 43 terms; removed
3 duplicates (`building services`, `ventilation`, `structural steel` already present);
sorted alphabetically. Final list: **101 terms**.

#### A3 — DISQUALIFY_PHRASES expansion
Merged 53 new phrases across three category groups:
- IT/Digital/Cyber (34 phrases): `product manager`, `automation engineer`, `test engineer`, `digital transformation`, `systems engineer`, etc.
- HR/Admin/Commercial (17 phrases): `hr manager`, `quantity surveyor`, `estimator`, `operations manager`, `sales manager`, etc.
- Healthcare/Life Sciences (6 phrases): `clinical project manager`, `pharmaceutical`, `pharma`, `biotech`, `medical device`, `clinical trial`

`_DISQUALIFY_RES` auto-regenerated via existing list comprehension — no separate edit needed.

#### A4 — Gold set extension and precision check
Added 13 new fixtures (7 positive, 6 negative):

**True positives** — sector coverage:
| Fixture | Title | Key new feature tested |
|---------|-------|----------------------|
| pos_10 | Commissioning Manager — HVAC & Mechanical | A1 `commissioning manager` |
| pos_11 | M&E Manager — Commercial Fit-Out | A1 `m&e manager` regex |
| pos_12 | Engineering Manager — Submarine (Defence) | A1 `engineering manager` + A2 `submarine` |
| pos_13 | Project Manager — CCGT Power Station | A2 `ccgt`/`combined cycle`/`gas turbine` |
| pos_14 | Project Engineer — Offshore Mechanical | A1 `project engineer` (exact EJL 0-storage fix) |
| pos_15 | Construction Manager (Mechanical Services) | A1 `construction manager` |
| pos_16 | Senior PM — Offshore Wind Farm BoP | A2 `wind farm`/`wind turbine`/`balance of plant` |

**True negatives** — noise kill:
| Fixture | Title | Key new feature tested |
|---------|-------|----------------------|
| neg_12 | Digital Project Manager — IT Transformation | A3 `digital project manager`/`digital transformation` |
| neg_13 | Product Manager — SaaS Analytics | A3 `product manager` (tech PM ≠ project manager) |
| neg_14 | HR Manager — Talent Acquisition | A3 `hr manager`/`human resources`/`talent acquisition` |
| neg_15 | Sales Manager — Engineering Products | A3 `sales manager` |
| neg_16 | Senior Quantity Surveyor — Construction | A3 `quantity surveyor` |
| neg_17 | Automation Engineer — RPA/Python | A3 `automation engineer` |

**Precision result:** 100% on extended gold set (36 TP, 0 FP for mech_filter).
Combined pm+mech precision: 100% (both thresholds beat ≥80% combined target).

### Recall boost on EJL (the key signal)
The new `project engineer` and `commissioning manager` PM_TITLE_RE alternations are the direct fix for Energy Jobline's 0-storage problem. The cached 2026-06-15 raw data from Michael's v0.1 EJL fetch still returns generic engineering roles (not PM-focused), so the DB shows 0 for that source — but pos_14 and pos_10 confirm that when EJL returns the right titles (after Michael's M4 multi-query), they will now pass `passes_pm_role`. This is the intended split: my filter expansion + Michael's M4 multi-query = EJL storage > 0.

### Pipeline smoke result
Re-processed cached 2026-06-15 raw data (`--skip-fetch`):
- **Stored: 37** (was ~26 with v0.1 filters — +42% on the same raw data)
- The additional 11 stored listings came from `engineering manager` / `commissioning manager` / `m&e manager` titles on other sources (Reed/Adzuna) that v0.1 rejected.

### Edge-case notes for future Ada work
1. **"operations manager" + mech keyword override**: For sector-based listings, the existing `passes_mechanical` override (if `has_disqualifier=True`, check for mech keyword in title) protects legitimate roles like "Operations Manager (HVAC Plant)" from being rejected at the mech gate. For generalist sector, disqualify_score (5) exceeds typical single-keyword mech_score (3), so standalone "Operations Manager" is correctly rejected. The pm_filter is the primary gate in any case (ops manager ≠ PM title).
2. **"quantity surveyor" in construction sector**: mech_filter passes (construction sector + "construction" keyword in title overrides QS disqualifier), but pm_filter rejects correctly. Documented in neg_16 fixture.
3. **"fixture" as MECH_KEYWORD**: Low-frequency risk of false positives from "light fixture" or "sporting fixture" in job descriptions. In practice, only fires for manufacturing jig-and-fixture contexts where other mech signals are also present.
4. **"site manager" and "engineering manager" alternations**: As Tommy noted, these are the riskiest additions to PM_TITLE_RE. The two-gate system (pm_filter loosened + mech_filter as safety net) is the correct mitigation. Monitoring recommended on first live v0.2 report.



### Problem
A listing *"ewi Recruitment — Project Manager (High Speed Rail) - MENA Region"* with explicit location `"Cairo, Cairo Governorate, Egypt"` appeared in the Review Queue of the 2026-06-15 live report. The previous filter only hard-rejected non-UK countries when `detect_country` returned a non-"GB" code — but Egypt was not in `_NON_UK_MAP`, so `detect_country("Cairo, Cairo Governorate, Egypt")` fell through to the "location present, no match → confirmed UK" branch and returned "GB". The UK filter then passed the listing (location present + country GB). It appeared in Review Queue only because of a "Day rate missing" sanity flag, not because the UK filter rejected it.

### Root cause
`_NON_UK_MAP` in `regex_fields.py` was missing an entry for Egypt/Cairo. Any listing with an explicit Egyptian location was silently treated as confirmed UK.

### Changes delivered

#### `regex_fields.py`
- Added Africa section to `_NON_UK_MAP`: `\b(egypt|cairo|alexandria)\b` → `"EG"`.
- Added `# example: "Cairo, Cairo Governorate, Egypt" → country="EG"` to module docstring.

#### `filters.py`
- Added `_KNOWN_NON_UK_CODES: frozenset[str]` computed from `_NON_UK_MAP` at module level.
- Rewrote `passes_uk` with three explicit cases:
  1. `country in _KNOWN_NON_UK_CODES` → hard-reject, **no flag**.
  2. `country == "GB"` and `location.strip()` → pass (confirmed UK).
  3. `country == "GB"` and no location → scan title/desc:
     - Non-UK signal found → hard-reject, no flag.
     - No signal → soft-reject + `country_unknown_assumed_non_uk` flag (Review Queue).
- Added safety net: any non-GB code not in `_KNOWN_NON_UK_CODES` → hard-reject, no flag.
- The sanity-flag path is now **only** reachable in case 3 (genuinely unknown) — never when positive non-UK evidence exists.

### Gold-set additions (3 new negative fixtures)
| File | Location | Expected country | Fail filter |
|------|----------|-----------------|-------------|
| `non_uk_cairo_explicit` | Cairo, Cairo Governorate, Egypt | EG | uk_filter |
| `non_uk_us_explicit` | New York, NY, USA | US | uk_filter |
| `non_uk_uae_explicit` | Dubai, UAE | AE | uk_filter |

### Test results
- Baseline: 128 passed → Post-change: 138 passed (+10; requirement was ≥ 131).
- 4 new dedicated unit tests: hard-reject for EG/US/AE, plus `detect_country` Egypt assertion.
- 0 failures.

### Live verification
- Deleted stale Cairo DB entry (was inserted before the filter fix).
- Re-ran `mechpm.cli run-all --skip-fetch`. 0 hits for "Cairo" or "Egypt" in `reports/2026-06-15.md`.

### Design notes
- `_KNOWN_NON_UK_CODES` is derived directly from `_NON_UK_MAP` at import time; adding a new country to the map automatically extends both detection and hard-reject coverage.
- The "soft-reject / Review Queue" path is now reserved exclusively for genuinely indeterminate listings (no location, no title/desc geo signal). All positively-identified non-UK listings are silently dropped.
- Decision logged at `.squad/decisions/inbox/ada-hard-reject-non-uk.md`.

---

## EJL T4 Gate Diagnosis & Fix (2026-06-15)

**Requested by:** Steve (steve-ms)  
**Problem:** v0.2 pipeline fetched 101 EJL listings but stored 0. T4 gate requires EJL ≥5 stored.

### Root cause (threefold)

1. **EJL's `?location=United+Kingdom` URL param is ineffective.** EJL is a global energy
   job board (191k listings). The location parameter doesn't filter results — 71/101 listings
   had confirmed non-UK locations (USA, Germany, Spain, Brazil, Italy etc.).

2. **`_NON_UK_MAP` had gaps.** `detect_country()` only covered ~12 countries. 19 non-UK
   listings (Spain, Italy, Brazil, China, Malaysia, Australia, Mexico, Czech Republic,
   Thailand, Mozambique, Colombia) incorrectly got `country="GB"` → latent precision bug.
   These 19 were then rejected by `passes_pm_role` (non-PM titles) but the UK guard
   should have stopped them first.

3. **PM_TITLE_RE too narrow for EJL's energy-sector vocabulary.** EJL UK listings use
   project-controls titles absent from PM_TITLE_RE: "Senior Planner", "Planning Engineer",
   "Contracts & Cost Control Engineer". These are legitimate PM-equivalent roles in oil &
   gas / power sectors.

### Fixes applied

| File | Change |
|------|--------|
| `energy_jobline.py` | Added `_is_clearly_non_uk()` adapter-level guard — drops confirmed non-UK before returning from `fetch()`. Regex covers country-suffixed location strings and "in [City, Country]" title patterns. |
| `regex_fields.py` | Expanded `_NON_UK_MAP` from ~12 → ~40 entries. Added Spain, Italy, Brazil, China, Malaysia, Australia, Mexico, Norway, Denmark, Sweden, Finland, Poland, Czech Republic, Belgium, Austria, Switzerland, Portugal, Romania, Colombia, Thailand, Mozambique, Nigeria, South Africa, Canada, New Zealand, Japan, South Korea. "ireland" changed to "republic of ireland" to avoid false-rejecting Northern Ireland listings. |
| `filters.py` | Extended `PM_TITLE_RE` with energy project-controls titles: `planning engineer`, `project planner`, `senior/lead planner`, `project controls manager/engineer/lead/specialist`, `cost control engineer`, `contracts? engineer`. |
| `config.toml` | EJL `keywords_list` changed to 4 focused keyword strings (without embedded "United Kingdom"), `max_pages_per_query=5`. |
| `tests/test_ejl_regression.py` | NEW: 44 regression tests covering `_is_clearly_non_uk()`, `detect_country()` extended, end-to-end filters, energy project-controls titles. |
| `tests/fixtures/gold_set/positive/` | NEW pos_17 (Senior Planner) + pos_18 (Planning Engineer) from EJL Blantyre listings. |
| `tests/test_filters.py` | Updated positive gold-set count assertion 16 → 18. |

### Before / after

| Source | Before | After |
|--------|--------|-------|
| Reed | 28 | 31 |
| RailwayPeople | 5 | 5 |
| Adzuna | 5 | 5 |
| **EJL** | **0** | **3** |
| Aviation | 2 | 2 |
| **Total** | **40** | **46** |

### T4 gate gap

Achieved 3 EJL stored (target was ≥5). EJL's live data at this run time only contained
3 UK PM-adjacent listings surfaceable by keyword search. The structural fix is complete
and correct; the residual gap reflects EJL's limited UK contract PM listing availability
at this point in time.

Proposed gate revision: **EJL ≥3 stored per run** (OR ≥5 accumulated over 7 days).
See `.squad/decisions/inbox/ada-ejl-t4-gate-root-cause.md` for full write-up.

### Test suite

- Baseline: 264 passed, 0 failed
- After fix: **326 passed, 25 skipped, 0 failed** (326 > 264 ✓)

### Key design learnings

- EJL embeds location in title format `"[Title] in [City, Country]"` — good geo signal.
- Adapter-level country guard is appropriate for sources with known global content pollution.
- In energy/oil & gas, PM function = multiple title patterns; PM_TITLE_RE must cover the full
  project-controls vocabulary (planning, cost control, contracts) not just "project manager".
- Reed also benefits from the PM_TITLE_RE expansion (+3 Reed listings for planning roles).
- Never embed "United Kingdom" in EJL keyword text — the site's FTS only returns listings
  containing that exact phrase in job body copy, which is rare.

---

## v0.2 Precision Fixes — Japan Leak + Site Manager False Positives (2026-06-15)

**Requested by:** Steve (steve-ms)  
**Pipeline state entering this session:** 46 stored.  
**Goal:** Two surgical precision fixes before v0.2 is declared done.

### Issue 1 — Japan location leak (passes_uk Case 2 defense-in-depth)

#### Root cause
`detect_country("Tokyo, Japan")` correctly returns "JP" — Japan IS in `_NON_UK_MAP` (added in the EJL T4 session). The real vulnerability was a dedup path: if a listing was originally stored when Japan was NOT yet in `_NON_UK_MAP`, the record has `country='GB'` on disk. Subsequent runs skip it via dedup (already-stored listings are never re-filtered). `passes_uk` Case 2 (`country == "GB" AND location.strip()`) returned True without ever re-scanning the location text — it trusted the stored country code blindly.

Even without dedup, if a future country mapping is added and an adapter incorrectly sets `country="GB"` for a non-UK listing, Case 2 would pass it silently.

#### Fix
`passes_uk` Case 2 now scans `listing.location` against `_NON_UK_MAP` before returning True. If any non-UK pattern matches in the location text, the listing is hard-rejected with no flag. This is defense-in-depth: it operates independently of the country field value.

Implementation detail: `_NON_UK_MAP` is already imported in `filters.py`; the existing pattern compilation loop was extended by one call at Case 2 evaluation time.

### Issue 2 — Site Manager precision (construction sector description gap)

#### Root cause
`passes_mechanical` for `_MECHANICAL_SECTORS` only checked the title for disqualifiers, then returned True if no disqualifier found. Descriptions were never scanned for sector-supervision signals. In the construction sector, "Site Manager" can mean a genuine mechanical PM (water treatment, M&E installation) or a labour/site-foreman supervisor (scaffolding, fit-out, CIS). The filter had no way to distinguish them when the title alone was ambiguous.

Two concrete false positives found in the 46-stored dataset:
1. **Reed "Site Manager" (Deeside)** — lab fit-out; SMSTS and First Aid certifications cited; zero MECH_KEYWORDS in visible description.
2. **Reed "Site Manager - Construction" (Swindon)** — "hands on role overseeing day to day site activities"; CIS rate; zero non-"construction" MECH_KEYWORDS.

#### Fix
1. Added three new entries to `DISQUALIFY_PHRASES`: `"smsts"`, `"hands on role"`, `"first aider"`. These are reliable site-foreman supervision signals.
2. Added a description-scan branch to `passes_mechanical` for construction-sector listings: when a disqualifier phrase is found in the description AND the title does NOT already have a disqualifier, check if the title carries a specific mech keyword (excluding "construction" itself as circular evidence for this sector). If no specific mech keyword in title → reject.

Guard: `if has_desc_disqualifier and not has_disqualifier:` — the description branch only fires when the title is CLEAN (no disqualifier). This avoids the `neg_16_quantity_surveyor` regression where "quantity surveyor" was in both title and description; the title already handled the disqualifier so the description branch must not fire.

#### Listings disposition
| Title | Location | Decision | Signal |
|-------|----------|----------|--------|
| Site Manager | Deeside | **DROP** | SMSTS + First Aid in desc, no mech keyword in title |
| Site Manager - Construction | Swindon | **DROP** | "hands on role" in desc, only "construction" in title (excluded as circular) |
| Mechanical Site Manager | Windsor | **KEEP** | "mechanical" in title overrides desc disqualifier |
| Site Manager (chemical plant) | Cambridge | **KEEP** | No desc disqualifier — passes cleanly |
| Site Manager | Keith | **KEEP** | `sector=rail` (railwaypeople source default); rail sector not in construction branch |
| Construction Manager | Port Talbot | **KEEP** | No desc disqualifier (no smsts/hands-on-role in truncated description) |
| Site Manager | Gloucester | **KEEP** | `sector=generalist`; construction branch not triggered |
| Site Manager | Windsor (M&E) | **KEEP** | `sector=generalist`; "mechanical" in description hits MECH_KEYWORDS |

#### Key constraint: "construction" as circular evidence
When the construction-sector description disqualifier fires, the title-override check excludes "construction" from the MECH_KEYWORDS set. Without this, "Site Manager - Construction" would override (title contains "construction" = MECH_KEYWORDS hit) even though that's just the sector label, not a domain qualifier.

### Test regression caught and fixed
`neg_16_quantity_surveyor`: title = "Senior Quantity Surveyor — Commercial Construction (Contract)" has "quantity surveyor" in DISQUALIFY_PHRASES. Description also mentions "quantity surveyor" → the new description-scan branch was firing. Fix: guard `if has_desc_disqualifier and not has_disqualifier:` — description branch only fires when title is clean.

### Results
- Test suite: **333 pass, 25 skip** (326 baseline + 7 new regression tests). 0 failures.
- Pipeline: **44 stored** (down from 46 — exactly the 2 false positives dropped). ≥40 threshold met.
- Commit: `fix(filter): Japan location leak + Site Manager precision fixes` (f14a30f)

### New regression tests added (7)
1. `test_passes_uk_tokyo_japan_location_stale_country_rejected` — Tokyo+Japan in location with stale country=GB → False
2. `test_passes_uk_tokyo_only_stale_country_rejected` — "Tokyo" alone in location with country=GB → False
3. `test_passes_uk_manchester_uk_still_passes_after_location_scan` — Manchester UK → True (regression guard)
4. `test_passes_mechanical_site_manager_smsts_in_desc_dropped` — construction sector, "Site Manager", SMSTS in desc → False
5. `test_passes_mechanical_site_manager_hands_on_role_in_desc_dropped` — "Site Manager - Construction", "hands on role" → False
6. `test_passes_mechanical_mechanical_site_manager_smsts_in_desc_kept` — "Mechanical Site Manager" with SMSTS → True
7. `test_passes_mechanical_site_manager_chemical_plant_desc_kept` — "Site Manager" with chemical plant in desc → True

### Decision drop
`.squad/decisions/inbox/ada-site-manager-precision.md` — documents smsts/hands-on-role disqualifier logic and the "construction as circular evidence" constraint for team review.
