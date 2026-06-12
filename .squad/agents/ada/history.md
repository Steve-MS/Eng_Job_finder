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

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: implement LLM extractor + dedup harness per Tommy's adapter contract. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.
