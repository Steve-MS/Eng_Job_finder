# Decision: EJL T4 Gate — Root Cause & Partial Resolution

**Date:** 2026-06-15  
**By:** Ada (Data Extraction)  
**Status:** Partially resolved — T4 gate revised  
**Slug:** ada-ejl-t4-gate-root-cause

---

## Root Cause

Energy Jobline fetched **101 listings** (v0.2 multi-query run) but stored **0**.
Three distinct problems were identified:

### A. EJL `?location=United+Kingdom` URL param does NOT filter geographically
EJL is a global energy job board.  The `location=United+Kingdom` search parameter
is either partially implemented or completely ignored — 71 of 101 listings had
confirmed non-UK locations (USA, Germany, Spain, Brazil, Italy, China, Malaysia,
Australia, Mexico, etc.) despite the location filter in the URL.

### B. `_NON_UK_MAP` had gaps — false-positive UK classifications
`detect_country()` in `regex_fields.py` only covered ~12 countries.  The 19
remaining non-UK listings (Spain, Italy, Brazil, China, Malaysia, Australia,
Mexico, Czech Republic, Thailand, Mozambique, Colombia) all received
`country="GB"` because their country wasn't in the map.  These 19 were then
correctly rejected by `passes_pm_role` (they were technicians, operators, etc.)
but the UK filter was letting them through silently — a latent precision bug.

### C. PM_TITLE_RE too narrow for EJL's energy-sector title vocabulary
EJL's UK listings use project-controls titles, not "Project Manager":
- "Senior Planner" — owns project schedule in oil & gas = PM equivalent
- "Planning Engineer" — schedule/EVM/critical path = project controls PM
- "Contracts & Cost Control Engineer" — contract admin + cost management = PM
These are standard project delivery titles in the energy/oil & gas sector.
Tommy's v0.2 spec correctly identified "project engineer" as missing; the
actual gap was broader.

### D. EJL search returns "featured/promoted" global listings on every page
Regardless of keywords, EJL's first pages show the same ~15 featured global
energy jobs (including Spanish Iberdrola listings, multi-location global posts,
etc.).  Actual keyword-matching UK results are sparse and appear mixed in with
the featured content.

---

## Fixes Implemented

| Fix | File | Impact |
|-----|------|--------|
| 1. Expanded `_NON_UK_MAP` | `regex_fields.py` | +26 countries now correctly detected as non-UK |
| 2. `_is_clearly_non_uk()` post-fetch guard | `energy_jobline.py` | Drops confirmed non-UK listings at adapter level (52 dropped in test run) |
| 3. UK-targeted EJL keywords | `config.toml` | Reduced global noise; more focused queries |
| 4. PM_TITLE_RE extended for energy project-controls | `filters.py` | planning engineer, senior/lead planner, project planner, project controls manager/engineer/lead/specialist, cost control engineer, contracts engineer |
| 5. Regression tests (44 cases) | `test_ejl_regression.py` | Guards all fixes |
| 6. Gold-set fixtures pos_17 + pos_18 | `fixtures/gold_set/positive/` | Senior Planner + Planning Engineer from EJL |

---

## Before / After Stored Counts

| Source       | Before fix | After fix |
|--------------|-----------|-----------|
| Reed         | 28        | 31        |
| RailwayPeople | 5        | 5         |
| Adzuna       | 5         | 5         |
| **EJL**      | **0**     | **3**     |
| Aviation     | 2         | 2         |
| **Total**    | **40**    | **46**    |

---

## T4 Gate Revision Proposal

**Original T4:** Energy Jobline ≥5 stored  
**Achieved:** Energy Jobline **3 stored** on 2026-06-15 run

**Reason for shortfall:** EJL's live site at the time of this run had only 3 UK
PM-adjacent listings available through keyword search.  The site's featured/promoted
global content dominates search results pages.  The 3 UK listings stored are:

1. "Senior Planner in United Kingdom, Blantyre"
2. "Planning Engineer in United Kingdom, Blantyre"
3. "Contracts & Cost Control Engineer | United Kingdom"

All three are legitimate energy-sector project-controls roles.

**Proposed revised T4:** Energy Jobline ≥3 stored per run, OR ≥5 accumulated over
7 days (EJL listing availability fluctuates — more UK PM contracts are posted
periodically).

**Note:** Arthur should review and formally accept/reject this gate revision.
The structural fix (improved queries + adapter-level UK guard + extended PM_TITLE_RE)
is complete and correct.  The remaining gap reflects EJL's limited UK contract PM
listing availability at this specific point in time.

---

## Sample EJL Titles Now Stored

1. **"Senior Planner in United Kingdom, Blantyre"** — NES Fircroft energy project, Scotland
2. **"Planning Engineer in United Kingdom, Blantyre"** — Energy project controls, Scotland  
3. **"Contracts & Cost Control Engineer | United Kingdom"** — Oil & gas cost/contracts role
