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


# Ada — History

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
