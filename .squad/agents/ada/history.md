# Ada — History

## 2026-06-15: v0.2 sprint complete

v0.2 query-slate expansion sprint concluded. Multi-session continuation:
- Filter taxonomy expansion (A1-A4): energy sector titles + country detection
- EJL root-cause fix (101→3 stored)
- Site Manager precision refinement
- rate_parser module (18 listings structured)
- 398 tests passing

**Final state: 44 stored across 5 sources, 18 with structured day-rate, 398 tests passing.**

## 2026-06-18: v0.4 — broad role expansion (16 new titles)

Scope broadening requested by Steve: add 16 new job title families beyond pure mechanical-engineering PM roles. These span commercial, strategic, sustainability, coaching, and social value domains.

**Key design decision:** Two-regex architecture.

`PM_TITLE_RE` is the gate for `passes_pm_role()`. All 16 new titles added as alternations.

`BROAD_ROLE_RE` is a new constant that matches the same 16 titles (plus `project planner` which was already in PM_TITLE_RE). It is checked at the TOP of `passes_mechanical()` — a title match returns `True` immediately, bypassing the mech keyword scorer entirely.

**Why this matters:** Roles like Executive Coach, Mental Health Lead, Social Value SME, Sustainability Lead have no mechanical vocabulary in their descriptions. The existing `passes_mechanical()` keyword scorer would reject them on generalist boards. BROAD_ROLE_RE provides a clean bypass that does not weaken precision for engineering roles.

**Fixture reclassification:** `neg_16_quantity_surveyor` moved to `pos_28_quantity_surveyor`. Was a negative because QS was a DISQUALIFY_PHRASE and PM_TITLE_RE didn't cover it. Now a desired target — expected outcomes updated to `pm_filter: true`, `mech_filter: true`, `overall_pass: true`.

**PM_TITLE_RE additions (15 new, project planner already present):**
- `programme director` — covers "Project Programme Director"
- `quantity surveyor`
- `(?:project )?cost accountant`
- `risk manager`
- `commercial manager`
- `coo | chief operating officer`
- `operations director`
- `business improvement (manager|director)`
- `sustainability (lead|manager|director)`
- `executive coach`
- `people development (coach|manager)`
- `mental health (lead|manager|advisor|director)`
- `innovation manager`
- `(tender|bid) writer`
- `social value (sme|manager|lead|specialist)`

**Config updates (config.toml, all active sources):**
- Reed `keywords_list`: +9 queries
- EJL `keywords_list`: +5 queries
- Adzuna `what_or`: +7 terms
- Michael Page `keywords_list`: +6 slugs
- Manpower Group `keywords_list`: +6 queries
- BAM Careers `keywords_list`: +4 queries
- Mace Group `keywords_list`: +4 queries
- Turner & Townsend `keywords_list`: +6 queries
- RailwayPeople `keywords_list`: new (was hardcoded) — 8 keyword queries
- Aviation Job Search `_RELEVANT_PATTERNS`: +6 URL slug patterns

**RailwayPeople adapter refactor:** Adapter previously had a single hardcoded `_SEARCH_URL`. Now accepts `keywords_list` from config, builds URLs via `_build_rp_search_url()`, iterates per keyword, deduplicates by `source_listing_id`. CLI updated to pass `keywords_list` to RailwayPeopleAdapter.

**Tests:** 761 passed (up from 479+), 25 skipped. 5 new parametrised test functions covering: pm_filter passes for all 32 broad-role title variants; mech_filter bypass for 21 variants; project planner regression guard; BROAD_ROLE_RE negative cases; QS disqualifier bypass end-to-end.

**Learnings:**
- When DISQUALIFY_PHRASES and new desired titles collide ("quantity surveyor"), the fix is: add to PM_TITLE_RE AND add BROAD_ROLE_RE bypass in passes_mechanical(). The disqualifier stays for its original purpose; the bypass fires first when the title IS the desired role.
- Gold-set fixtures encode the filter state at a point in time, not timeless truth. When scope deliberately changes, reclassify fixtures rather than masking failures.
- Hardcoded adapter URLs are a config-coupling debt. The EJL `keywords_list` pattern applied cleanly to RailwayPeople with one helper function and one new CLI branch.

## 2026-06-17: v0.3 — broader role families

Extended `PM_TITLE_RE` with two new role families (plus project engineer variant verification):

**Assurance family** (new PM_TITLE_RE alternations):
- `assurance manager/engineer/lead/advisor`
- `quality assurance`, `safety assurance`, `nuclear assurance`, `independent assurance`
- `SQA` abbreviation (safety/quality assurance)

**Document Controller family** (new PM_TITLE_RE alternations):
- `document controller`, `document control manager/lead`, `document management`
- `records controller/manager`

**Project Engineer variants** — verified: existing `project engineer` alternation already covers
all seniority prefixes (senior/lead/principal/junior/assistant) via `re.search` substring match.

**Config updates:**
- Reed `keywords_list`: +3 queries (assurance engineer mechanical, document controller engineering, project engineer mechanical)
- EJL `keywords_list`: +2 queries (assurance engineer, document controller)
- Adzuna `what_or`: +2 terms (`assurance`, `controller`)
- Aviation Job Search `_RELEVANT_PATTERNS`: +2 URL slug patterns (assurance, document-control)

**Gold-set fixtures:** +9 positives (pos_19–pos_27), +2 negatives (neg_18–neg_19). Fixed pos_23
to remove "permanent way" (rail term that falsely triggered perm signal).

**Tests:** 479 passed, 25 skipped (up from 398).

**Pipeline run (2026-06-17):** 51 stored (up from 44). New role samples:
- Document Controllers: 2 stored
- Assurance: 1 stored (Quality Assurance Engineer, reed)
- Project Engineer variants: 5 stored

**Precision drop:** `.squad/decisions/inbox/ada-broader-roles.md`.

