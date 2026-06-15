# Decision: Site Manager Precision — Construction Sector Description Disqualifier

**Date:** 2026-06-15  
**Author:** Ada  
**Status:** Implemented  
**Area:** `src/mechpm/extractor/filters.py`

---

## Context

Tommy's v0.2 Risk 1 (`.squad/decisions/inbox/tommy-v02-query-slate.md`, Section 5) flagged
that adding `site manager` to `PM_TITLE_RE` could pull in labour-supervisor roles. After the
v0.2 pipeline ran at 46 stored, an audit confirmed two false positives:

1. **Reed "Site Manager" (Deeside)** — multi-skilled site manager on a lab fit-out. Required
   certifications: SMSTS and First Aid. Zero MECH_KEYWORDS hits in visible description.
2. **Reed "Site Manager - Construction" (Swindon)** — "hands on role overseeing day to day site
   activities". CIS contractor rate. Zero MECH_KEYWORDS beyond "construction" (which is circular
   evidence for a construction-sector listing).

Both listings: `sector=construction`, title clean (no existing disqualifier), description
correctly identifies them as labour-supervisor roles rather than mechanical PM roles.

---

## Decision

### 1. Add site-supervisor disqualifiers to `DISQUALIFY_PHRASES`

Three new entries:
- `"smsts"` — Site Management Safety Training Scheme; typically cited as a foreman/site-manager
  credential in construction supervision, not mechanical engineering management
- `"hands on role"` — phrasing associated with labour-supervisor / site-foreman roles
- `"first aider"` — cited as required certification for site-level supervision; not a mechanical
  PM requirement

These phrases join the existing `DISQUALIFY_PHRASES` list and are pre-compiled into `_DISQUALIFY_RES`
automatically via the existing list comprehension.

### 2. Construction-sector description-scan branch in `passes_mechanical`

When `sector == "construction"` (or any `_MECHANICAL_SECTORS` value) and the title is CLEAN
(no disqualifier already found in title), scan the description for disqualifier phrases. If a
match is found, require the title to carry a specific mech keyword — **excluding "construction"
itself as circular evidence for this sector**.

Logic:
```python
if has_desc_disqualifier and not has_disqualifier:
    mech_keywords_not_construction = [k for k in MECH_KEYWORDS if k != "construction"]
    title_has_specific_mech = any(k in title_lower for k in mech_keywords_not_construction)
    if not title_has_specific_mech:
        return False
```

This runs only in the construction-sector branch, only when the title is clean, and only when
at least one description disqualifier fires.

### 3. "construction" excluded from title override check

When the description-level disqualifier fires for a construction-sector listing, "construction"
in the title is NOT sufficient to override — it is the sector label, not a domain qualifier.
Only a specific MECH_KEYWORDS hit (e.g., "mechanical", "hvac", "piping", "pumping") can override.

---

## Alternatives considered

| Alternative | Reason rejected |
|---|---|
| Require MECH_KEYWORDS in description for all construction-sector listings | Too aggressive — 9/14 construction listings would drop including legitimate ones with sparse description text (Reed truncates to ~700 chars) |
| Add title qualifier requirement ("Mechanical Site Manager" = KEEP; bare "Site Manager" = DROP) | Too binary — "Construction Manager" (Port Talbot major industrial) is a valid KEEP with no mech qualifier in title |
| MECH_KEYWORDS threshold > 0 in description | Reed API truncation makes description-level mech evidence unreliable; threshold approach would be fragile |
| Separate disqualifier list for description vs title | Over-engineering; the existing DISQUALIFY_PHRASES list is already used in both contexts cleanly |

---

## Listing disposition (all 8 audited)

| Title | Location | Sector | Decision | Reason |
|-------|----------|--------|----------|--------|
| Site Manager | Deeside | construction | **DROP** | SMSTS + First Aid in desc; no mech keyword in title |
| Site Manager - Construction | Swindon | construction | **DROP** | "hands on role" in desc; "construction" in title is circular |
| Mechanical Site Manager | Windsor | construction | **KEEP** | "mechanical" in title overrides desc disqualifier |
| Site Manager (chemical plant desc) | Cambridge | process | **KEEP** | No desc disqualifier; process sector (not construction) |
| Site Manager | Keith | rail | **KEEP** | `sector=rail` (railwaypeople default); branch not triggered |
| Construction Manager | Port Talbot | construction | **KEEP** | No smsts/hands-on-role in visible description |
| Site Manager | Gloucester | generalist | **KEEP** | Generalist sector; branch not triggered |
| Site Manager | Windsor (M&E) | generalist | **KEEP** | Generalist sector; "mechanical" in description |

---

## Impact

- **Precision:** 2 false positives dropped (Deeside lab fit-out, Swindon CIS supervisor).
- **Recall:** 6 genuine site manager listings retained. No false negatives introduced.
- **Pipeline count:** 46 → 44. ≥40 threshold met.
- **Test suite:** 326 → 333 pass. 7 new regression tests. 0 failures.

---

## Related

- Companion fix: `passes_uk` Case 2 defense-in-depth location scan (Japan leak — same commit).
- Tommy's v0.2 Risk 1: `.squad/decisions/inbox/tommy-v02-query-slate.md`, Section 5.
- "operations manager" override note: already in Ada history.md (2026-06-15 A3 learnings).

---

## Awaiting

- Tommy / Steve: confirm 44 stored is acceptable for v0.2 sign-off (was 46; -2 precision drops).
- Team: if further construction-sector false positives appear, the pattern to follow is expanding
  `DISQUALIFY_PHRASES` with new site-supervision signals. The description-scan branch will pick
  them up automatically.
