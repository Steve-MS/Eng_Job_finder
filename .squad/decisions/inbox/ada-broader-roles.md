# Ada — Precision Risk: Broader Role Families in PM_TITLE_RE

**Date:** 2026-06-17
**By:** Ada (Data Extraction)
**Status:** Proposed

## What changed

`PM_TITLE_RE` in `filters.py` was extended with two new role families:

1. **Assurance family** — `assurance manager/engineer/lead/advisor`, `quality assurance`,
   `safety assurance`, `nuclear assurance`, `independent assurance`, `SQA`
2. **Document Controller family** — `document controller`, `document control manager/lead`,
   `document management`, `records controller/manager`

`config.toml` was also updated to surface these roles at fetch time (Reed + EJL keywords;
Adzuna `what_or` extended with `assurance` and `controller`).

## Precision risks identified

### Risk 1: Non-mechanical assurance roles slipping through

The assurance family is broad. "Assurance Manager" titles appear in IT governance, cyber
security, financial services (internal audit), and pharma (QA) — all non-mechanical contexts.

**Mitigation:** `passes_mechanical` acts as the backstop. For generalist-sector listings,
`mech_score >= 1` is required. IT/cyber assurance roles have zero mechanical keywords and
are rejected. Nuclear/energy/rail assurance roles score strongly on mech domain keywords.

**Residual risk:** A vague "Quality Assurance Engineer" in a generalist sector listing with
sparse description could slip through if even one mech keyword is present (e.g., the word
"manufacturing" appears in boilerplate). Live observation: 1 live "Quality Assurance
Engineer" (reed) passed and was stored — this appeared legitimate based on sector context.

**Recommended watch:** Monitor for non-mechanical QA engineers in reports. If frequency rises,
add `quality assurance engineer` to DISQUALIFY_PHRASES conditional on no manufacturing/mech
keyword in title.

### Risk 2: "Document Controller" scope too wide via Adzuna `what_or`

The Adzuna OR term `controller` matches any job containing that word — including Financial
Controller, Stock Controller, etc. These are caught by the PM_TITLE_RE check (they don't
match `document controller` or `records controller`) so they will fail `passes_pm_role` and
be filtered out. Low actual risk.

**Mitigation:** `passes_pm_role` (title regex) is the gate; `controller` in `what_or` only
increases *fetch* volume, not stored count.

### Risk 3: "document controller" in IT/software context

"Document Controller (Software Products)" or similar tech-sector roles pass `passes_pm_role`
but fail `passes_mechanical` (mech_score=0). Added as a negative gold-set fixture (neg_19).
Confirmed rejected by the filter.

**Mitigation:** Confirmed working. No action needed.

### Risk 4: SQA abbreviation matches unexpected contexts

`\bsqa\b` is a 3-letter abbreviation. It won't match via body text (body signals fallback
uses `PM_BODY_SIGNALS`, not the title regex). In title context, SQA appearing standalone
(e.g., "SQA Test Engineer") would pass `pm_filter` but fail `mech_filter` (software testing).

**Mitigation:** `passes_mechanical` rejects software QA. Acceptable precision profile.

## Before / after stored count

| Run date   | Stored |
|------------|--------|
| 2026-06-15 | 44     |
| 2026-06-17 | 51     |

Delta: +7. New role samples stored this run:
- Document Controller: "Engineering Document Controller" (adzuna), "Document Controller- 12 month contract" (reed)
- Quality Assurance: "Quality Assurance Engineer" (reed)
- Project Engineer: "Senior Project Engineer", "Senior Project Engineer (E&P)", "Junior Project Engineer", "Facilities Project Engineer" (reed), "Senior Project Engineer (Civils)" (railwaypeople)

## Recommendation

No immediate action required. Monitor `Quality Assurance Engineer` precision over the next
2–3 runs. If non-mechanical QA engineers appear in the report, add a conditional disqualifier.
