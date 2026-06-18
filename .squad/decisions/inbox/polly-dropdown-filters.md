# Decision: Filter UI Refactor — Pill Buttons → Dropdown Selects

**Date:** 2026-06-18  
**Author:** Polly (Reporting & Domain)  
**Requested by:** Steve  
**Status:** Implemented ✅

---

## Problem

The HTML report had three separate pill-button filter bars stacked vertically (Source, Region, Job Type). Each used a multi-toggle pattern where every value was a button that could be individually activated/deactivated. This worked but was visually bulky (many buttons on a wide bar) and the interaction model was non-standard — single-dimension selection required knowing to click an already-active item to deselect it.

## Decision

Replace all three pill-button filter bars with a **single combined filter row containing three `<select>` dropdown elements** — one per dimension (Source, Region, Job Type).

## Rationale

1. **Simpler interaction model.** Dropdowns are the browser-native control for "pick one from a list". Users pick one source OR "All", one region OR "All", one job type OR "All". No need to explain the multi-toggle concept.
2. **Compact UI.** Three labelled selects in a single flex row takes far less vertical space than three stacked pill bars with 5–9 buttons each.
3. **Forward-compatible.** Options are dynamically discovered from the report's listings, so Ada's new job-type categories auto-appear in the dropdown with zero Polly code changes.
4. **Reduced CSS.** ~50 lines of per-source/per-region/per-jobtype active-state pill CSS removed; replaced with ~25 lines of simple select styling.
5. **Simpler JS.** Previous JS maintained three `Set` objects with complex toggle/reset logic (~80 lines). New JS reads `select.value` (empty string = All) and ANDs three conditions (~20 lines).

## Trade-offs accepted

- **No multi-select.** The old pill UI allowed selecting multiple sources simultaneously (e.g. Reed + Adzuna). The new dropdowns are single-select only. Accepted because Steve's primary use case is "show me only remote roles" or "show me only Project Managers" — exclusive drilldowns, not multi-combinations.
- **Count display.** Old pills showed counts as button text (Reed (28)). New dropdowns show counts as option text (Reed (28) in the `<option>` label). Functionally identical; slightly less prominent.

## Implementation

### Files changed

| File | Change |
|------|--------|
| `src/mechpm/reporter/html_render.py` | Replaced 3 pill functions + pill CSS + complex JS with `_render_combined_filter_bar()` + dropdown CSS + simple select JS |
| `tests/test_html_report.py` | Updated tests 12, 15, 16, 19, 21 to assert on `<select>` / `<option value>` patterns instead of pill `data-*` attributes |

### HTML structure

```html
<div id="filter-bar" class="filter-bar" style="display:none">
  <div class="filter-group">
    <label class="filter-bar-label" for="filter-source">Source:</label>
    <select id="filter-source" class="filter-select">
      <option value="">All Sources</option>
      <option value="reed">Reed (28)</option>
      <option value="adzuna">Adzuna (6)</option>
      ...
    </select>
  </div>
  <div class="filter-group">
    <label class="filter-bar-label" for="filter-region">Region:</label>
    <select id="filter-region" class="filter-select">
      <option value="">All Regions</option>
      <option value="london">London (6)</option>
      ...
    </select>
  </div>
  <div class="filter-group">
    <label class="filter-bar-label" for="filter-jobtype">Job Type:</label>
    <select id="filter-jobtype" class="filter-select">
      <option value="">All Job Types</option>
      <option value="project-manager">Project Manager (31)</option>
      ...
    </select>
  </div>
  <span id="filter-count" class="filter-count-label">Showing all 51 listings</span>
</div>
```

### JavaScript pattern

```js
function applyFilter(){
  var src = selSrc ? selSrc.value : '';
  var rgn = selRgn ? selRgn.value : '';
  var jt  = selJt  ? selJt.value  : '';
  var vis = 0;
  allCards.forEach(function(c){
    var show = (!src || c.dataset.source === src)
      && (!rgn || c.dataset.region === rgn)
      && (!jt  || c.dataset.jobtype === jt);
    c.style.display = show ? '' : 'none';
    if(show) vis++;
  });
  ...
}
```

Empty string = "All" (default `<option value="">`). The three filters AND together.

### Unchanged

- `data-source`, `data-region`, `data-jobtype` on `<article class="role-card">` — preserved
- `id="filter-bar"` — preserved (test 13 continues to pass)
- `applyFilter` function name — preserved (test 13 continues to pass)
- Progressive enhancement: bar hidden until JS runs; with JS disabled all cards visible

## Test result

25/25 tests in `tests/test_html_report.py` pass. 0 failures.
