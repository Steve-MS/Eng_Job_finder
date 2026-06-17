# Ada — History

## 2026-06-15: v0.2 sprint complete

v0.2 query-slate expansion sprint concluded. Multi-session continuation:
- Filter taxonomy expansion (A1-A4): energy sector titles + country detection
- EJL root-cause fix (101→3 stored)
- Site Manager precision refinement
- rate_parser module (18 listings structured)
- 398 tests passing

**Final state: 44 stored across 5 sources, 18 with structured day-rate, 398 tests passing.**

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

