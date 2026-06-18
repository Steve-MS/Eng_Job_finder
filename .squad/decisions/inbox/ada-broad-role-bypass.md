# Decision: Broad-Role Bypass — Mechanical Filter Exemption for 16 New Title Families

**Date:** 2026-06-18T10:52:00Z
**Author:** Ada
**Area:** `src/mechpm/extractor/filters.py`, `config.toml`, adapters, tests

---

## Context

Steve requested 16 new job title families be added to search criteria. Many of these roles (Executive Coach, Mental Health Lead, Sustainability Lead, Social Value SME, Tender/Bid Writer, Innovation Manager, People Development Coach) are not engineering roles and carry no mechanical vocabulary in their descriptions. The existing `passes_mechanical()` keyword scorer would reject all of them on a generalist job board.

The remaining titles (Quantity Surveyor, Risk Manager, Commercial Manager, Operations Director, Programme Director, Cost Accountant, Project Planner, COO/Interim COO, Business Improvement Manager/Director) are adjacent to engineering programmes but similarly do not guarantee mechanical keywords in their descriptions.

Per Steve's explicit instruction: **all 16 titles should bypass the mechanical filter** when the listing title matches.

---

## Decision

### Two-regex architecture

1. **`PM_TITLE_RE`** — existing gate for `passes_pm_role()`. All 16 new titles added as alternations. Existing engineering PM path unchanged.

2. **`BROAD_ROLE_RE`** — new constant in `filters.py`. Matches the same 16 titles plus `project planner` (already in PM_TITLE_RE; added here for bypass coverage). Used exclusively by `passes_mechanical()`.

### Bypass placement

`passes_mechanical()` checks `BROAD_ROLE_RE.search(listing.title)` as its **first statement**. On a match, it returns `True` immediately — before sector classification, before DISQUALIFY_PHRASES scan, before keyword scoring.

```python
def passes_mechanical(listing: NormalizedListing) -> bool:
    # Broad-role bypass: new role families pass without mechanical keywords.
    if BROAD_ROLE_RE.search(listing.title or ""):
        return True
    # ... existing sector-based + keyword-scoring logic unchanged
```

This placement:
- Does NOT weaken precision for engineering roles (the bypass only fires if the TITLE matches BROAD_ROLE_RE)
- Handles the "quantity surveyor in DISQUALIFY_PHRASES" conflict without removing the disqualifier (which still protects against non-QS roles that mention QS work)
- Is transparent and testable

---

## Alternatives considered

| Option | Verdict |
|--------|---------|
| Add broad roles to MECH_KEYWORDS | Rejected — "executive coach" is not a mechanical engineering keyword; would create false positives for engineering roles |
| Remove "quantity surveyor" from DISQUALIFY_PHRASES | Partial — removes the conflict for QS but doesn't solve the broader non-engineering roles problem |
| New fifth filter `passes_broad_role()` | Rejected — adds pipeline complexity; the bypass inside `passes_mechanical()` is cleaner since the result is identical |
| Sector-level bypass (add "coaching", "sustainability" sectors) | Rejected — sector detection is based on MECH_KEYWORDS and source verticals; adding non-engineering sectors would break sector-assignment logic throughout the pipeline |

---

## Acceptance impact

- `pm_filter` recall: increases (15 new title patterns in PM_TITLE_RE)
- `mech_filter` recall: increases (16 bypass paths)
- `pm_filter` precision: conserved — bypass titles are narrow, high-specificity phrases
- `mech_filter` precision: marginal risk; mitigated by requiring full title phrase match (word-boundary anchored), not substring

`neg_16_quantity_surveyor` reclassified to `pos_28_quantity_surveyor`. Fixture counts updated: 27 positives, 21 negatives.

---

## Files changed

| File | Change |
|------|--------|
| `src/mechpm/extractor/filters.py` | +15 alternations in PM_TITLE_RE; `BROAD_ROLE_RE` constant; bypass at top of `passes_mechanical()` |
| `config.toml` | `keywords_list` additions for reed, ejl, adzuna, michael_page, manpower_group, bam_careers, mace_group, turner_townsend; new `keywords_list` for railwaypeople |
| `src/mechpm/adapters/railwaypeople.py` | `_build_rp_search_url()` helper; `keywords_list` param in `__init__`; multi-keyword fetch with dedup |
| `src/mechpm/adapters/aviation_job_search.py` | +6 URL slug patterns in `_RELEVANT_PATTERNS` |
| `src/mechpm/cli.py` | `railwaypeople` now passes `keywords_list` (was falling through to `else` branch) |
| `tests/test_filters.py` | 5 new test functions (135 → fixture count updated 26/22 → 27/21) |
| `tests/fixtures/gold_set/positive/pos_28_quantity_surveyor.*` | Reclassified from neg_16; expected updated |

---

## Open questions / deferred

- Arthur gate check: precision thresholds for pm_filter (≥0.92) and mech_filter (≥0.96) should be re-validated against the full gold set once the next pipeline run completes.
- Some board-level sources (railwaypeople, aviation job search) are sector-specific. "Executive Coach" queries on railwaypeople.com will likely return 0 results — low cost, no harm, but worth monitoring.
- "Social Value SME" is a nascent title with no established search vocabulary on major boards. May benefit from body-signal expansion in a future sprint.
