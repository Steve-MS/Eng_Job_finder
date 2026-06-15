# Decision: HTML Report Renderer

**Date:** 2026-06-15  
**Author:** Polly (Reporting & Domain)  
**Request from:** Steve (steve-ms)  
**Commit:** `feat(reporter): add HTML report renderer with clickable role links`

---

## Problem

The Markdown report (`reports/2026-06-15.md`) contained `[View Full Listing](url)` links,
but these are not clickable when viewing a raw `.md` file outside a Markdown renderer.
Steve wanted a real HTML file he could open in a browser and click through directly to each role.

---

## Decision: Generate Both Formats Every Run

**Chose:** Emit `reports/{date}.md` AND `reports/{date}.html` on every pipeline run.

**Rejected:** HTML-only output.

**Rationale:**
- Markdown remains the diff-friendly artefact for version control and quick review.
- HTML provides the human-facing browseable view with full clickability.
- No new CLI flag is needed — both files are produced by `generate_report()` automatically.
- Zero change to any existing downstream consumer of the Markdown file.

---

## Implementation

### New file: `src/mechpm/reporter/html_render.py`

Public entry point: `render_weekly_html(listings, run_metadata, output_path) -> Path`

- Single self-contained HTML file; CSS embedded in `<style>`; no external assets; no JS.
- Reuses all existing business logic without modification:
  - `domain.effective_day_rate()`, `domain.rate_context()`
  - `grouping.is_premium()`, `grouping.is_urgent()`, `grouping.is_geo_flagged()`
  - `grouping.group_by_region()`, `grouping.get_soft_notes()`, `grouping.get_sanity_reasons()`
  - `render._rate_str()`, `render._duration_str()`, `render._start_str_safe()`
  - `render._source_name_from_url()`, `render._classify_seniority()`
  - `render._REGION_FLAGS`, `render._SENIORITY_LABELS`
- Output is byte-identical for identical inputs (deterministic ordering throughout).

### Wiring: `src/mechpm/reporter/generate.py`

Added one call after the existing `render_weekly()` call:

```python
html_output_path = Path(reports_dir) / f"{date_str}.html"
render_weekly_html(listings, run_metadata, html_output_path)
```

No changes to `pipeline.py`, `cli.py`, or any adapter.

---

## Visual Design

| Element | Style |
|---------|-------|
| Layout | Max 960px centred, white cards on #f7f8fa page |
| IR35 outside | Green pill (#dcfce7 / #166534) |
| IR35 inside | Amber pill (#fef3c7 / #92400e) |
| IR35 umbrella | Light-blue pill (#dbeafe / #1e40af) |
| IR35 not-stated | Grey pill (#f3f4f6 / #374151) |
| Rate-band tag | Small grey pill (subtle, below rate figure) |
| New card stripe | Blue left border (#2563eb) |
| Premium card stripe | Purple left border (#7e22ce) |
| Urgent card stripe | Orange left border (#ea580c) |
| Review card stripe | Amber left border (#b45309) |
| Flag pills | Colour-matched to card stripe |
| Title link | Blue (#1a56db), hover underline |
| View Listing btn | Outlined blue, fills on hover |
| Summary tiles | 5-tile grid: New / Urgent / Premium / Under Review / Total |

---

## Clickable Links

- **Role title** → primary source URL (first in `source_urls`)
- **"View Full Listing" button** → source URL (one button per URL)
- **Cross-source dedup listings** → all URLs rendered as named source links

---

## Tests Added (`tests/test_html_report.py`, +14 tests)

| Test | Assertion |
|------|-----------|
| `test_every_card_has_source_link` | `<a href>` exists pointing to source_url |
| `test_title_is_linked_to_source_url` | Title `href` matches source_url |
| `TestIR35Badges` (×5) | outside / inside / umbrella / None / "not_stated" → correct CSS class |
| `test_premium_listing_in_premium_section` | Premium URL appears before pipeline section |
| `test_multi_source_urls_all_clickable` | Both URLs in `source_urls` appear as `href` |
| `test_render_is_deterministic` | Two renders → byte-identical output |
| `test_html_document_structure` | DOCTYPE, `<style>`, `<footer>`, `report-wrap` present |
| `test_urgent_listing_in_urgent_section` | Urgent section exists; URL present |
| `test_review_queue_listing_has_reasons` | Geo-flagged listing → review-queue section; reason text |
| `test_hourly_rate_displays_per_hr` | `rate_period='hour'` → `£46/hr` in HTML |

**Test delta:** 398 → 412 passed, 25 skipped, 0 failures.

---

## Live Output

`reports/2026-06-15.html` — 146KB, 44 role cards, 45 unique clickable links.
