# Orchestration Log: Coordinator (Steve's Directive Capture)

**When:** 2026-06-12T16:30:00Z UTC  
**Agent:** Coordinator  
**Mode:** Sync (captured from user message)  
**Task:** Capture and document Steve's directive to extend MVP source coverage to vertical-specialist boards

## Outcome
**Status:** ✅ Complete

**Deliverable:** `.squad/decisions/inbox/copilot-directive-vertical-source-coverage.md`

## What was captured

**Directive:** Extend Michael's generalist UK job-source shortlist to include **vertical-specialist boards** covering transport (rail, aerospace, maritime, defence), construction/M&E, and energy sectors where mechanical-engineering PM roles cluster heavily.

**Rationale:** Generalist boards (Reed, Totaljobs, CWJobs) under-represent mechanical PM opportunities in verticals. Mechanical engineering is inherently sector-specific (rail infrastructure, aerospace platforms, energy projects, construction M&E); vertical-specialist boards reach candidates and clients that rarely post to generalist boards.

**Scope Impact:**
- **MVP target:** 7-source Tier-1 (3 generalist + 4 vertical-specialist)
- **Generalist 3:** Reed, Totaljobs, CWJobs (as per Michael's shortlist)
- **Vertical 4:** RailwayPeople (rail), Energy Jobline (energy), The Engineer Jobs (cross-sector + construction), Aviation Job Search (aerospace/defence)

**Domain Filter Implication:** Ada's mechanical-engineering domain filter must accept listings tagged "rail PM", "energy PM", "construction PM" even if "mechanical engineering" is not explicitly mentioned, provided role substance is mechanical project management in those sectors.

## Why this matters
Unlocks MVP coverage of ~60% of UK mechanical PM contract market (vs ~40% on generalist boards alone). Aligns source strategy with domain reality. Defers broader vertical exploration (automotive, maritime, construction sub-specialties) to v0.2 validation phase.

## Files Produced
- `.squad/decisions/inbox/copilot-directive-vertical-source-coverage.md` (~430 bytes)

## Next Steps
Michael incorporates vertical sources into shortlist. Ada adjusts domain filter rules. Tommy/Michael implement 4 new adapters (RailwayPeople, Energy Jobline, The Engineer Jobs, Aviation Job Search) alongside generalist 3.

---

**Note on Coordinator Role:** The Coordinator function is responsible for capturing user directives (Steve's input) and translating them into actionable decisions for the squad. This log documents that capture and ensures visibility for downstream agents (Tommy, Michael, Ada) on next spawn cycle.
