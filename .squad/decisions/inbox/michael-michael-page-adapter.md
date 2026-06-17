# Decision: Michael Page Adapter — Fetch Strategy and URL Pattern

**Agent:** Michael (Backend / Scraping)
**Date:** 2026-06-17
**Slug:** michael-michael-page-adapter

---

## Context

Michael Page (michaelpage.co.uk) was probed and confirmed scrape-able:
- Cloudflare in CDN-only mode (not blocking httpx)
- robots.txt allows `/jobs` and `/jobs/{slug}` paths; blocks `/jobs/*/*/*/` (4+ segments)
- Standard HTML (Drupal CMS), not SPA — no `__NEXT_DATA__`

## Decision

**Fetch strategy: Drupal path-based HTML scrape** (`/jobs/{keyword-slug}`)

### Key choices

| Concern | Decision | Rationale |
|---|---|---|
| Search URL | `/jobs/{slug}` (path-based, spaces→hyphens) | Drupal redirect from `?search=` always produces this path; direct path is canonical |
| Contract filter | None at URL level — capture all, pipeline filters | `?contract=Interim` / `?contract=temp` splits across 2 URLs per keyword; pipeline `passes_contract()` already handles this cleanly |
| Pagination | `?page=N` (0-indexed); page 0 has no param | Confirmed from `ul.pager__items a[rel=next]` in live HTML |
| Max pages | 3 per keyword (default) | ~30 results/page × 3 pages = up to 90 per keyword |
| Crawl delay | 5 s between requests | Courtesy delay; robots.txt does not mandate one |
| Dedup | By `source_listing_id` (div.job-title id attr) across keywords | Avoids cross-keyword duplicates |

### DOM fields confirmed (2026-06-17)

| Field | Selector |
|---|---|
| listing_id | `div.job-title` id attr (numeric node ID, e.g. `9282906`) |
| title | `div.job-title h3 a` text |
| url | `div.job-title h3 a` href → prepend base URL |
| location_raw | `div.job-location` text (after FA icon) |
| contract_type_raw | `div.job-contract-type` text: "Interim" / "Temporary" / "Permanent" |
| salary_raw | `div.job-salary` text (optional; "£320 - £375 per day" or annual) |
| description_raw | `div.job_advert__job-summary-text` + `div.job_advert__job-desc-bullet-points` |
| posted_at | Not available in search cards → always `None` |

### Config keywords (confirmed working Drupal paths 2026-06-17)

`project-manager`, `project-engineer`, `project-director`, `contracts-manager`, `programme-manager`

Keywords that 404: free-text multi-word slugs (e.g. `document-controller`, `assurance-engineer`). Logged at WARNING (page 0) or DEBUG (subsequent pages) and skipped gracefully.

## Outcome

- Smoke test: **78 listings** across 3 keywords (25 Interim/Temporary, 53 Permanent, 8 day-rate)
- Permanent listings are filtered by `passes_contract()` in the pipeline
- 49 unit tests passing
- Full suite: 575 passed, 25 skipped
