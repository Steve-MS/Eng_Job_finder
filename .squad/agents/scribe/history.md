# Scribe — History

## Project Seed (2026-06-12)
- **Project:** Mech-PM-Finder
- **Owner:** Steve
- **My role:** Silent session logger. I merge decisions, write orchestration + session logs, and commit `.squad/` state.

## Learnings
<!-- Append new learnings here. -->

## 2026-06-12: Tommy Lockout Patch Merged and Committed

**Session:** tommy-lockout-patch  
**Outcome:** Merged decision inbox, wrote orchestration log, wrote session log, appended Ada note, staged strict allow-list paths, committed and pushed.

**Rationale:** Tommy patched Ada's two filter defects under strict reviewer-lockout. UAE country detection and civil/mech disambiguation fixed. pytest: 3 failed → 0 failed.

**Decisions merged:** tommy-lockout-patch-2026-06-12.md  
**Inbox files processed:** 1  
**Decision archive gate:** Skipped (decisions.md < 20 KB)  
**History summarization gate:** Skipped (all < 15 KB)

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
