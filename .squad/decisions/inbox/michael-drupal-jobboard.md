# Decision: Drupal Job Board Adapter (Building4Jobs, NCE Careers, Careers in Construction)

**Date:** 2026-06-18
**Author:** Michael (backend/scraping)
**Status:** Shipped

---

## Context

Steve requested a shared adapter for 3 UK construction/civil job boards all described as running
"Drupal + Search API." Live recon on 2026-06-18 revealed a more complex reality.

---

## Discovery Findings

### Building4Jobs (www.building4jobs.com)
- **Platform:** Next.js / Jobiqo (NOT Drupal)
- **Data source:** `__NEXT_DATA__` SSR JSON at `props.pageProps.data.jobs.pages[]`
- **Pagination:** 1-indexed (`page=0` and `page=1` both return first 10 items; `page=2` is second)
- **Page size:** 10 items
- **Field types (Python JSON):** `address → list[str]`, `salaryRange → list[Term]`, `published → ISO 8601`
- **No bot protection; robots.txt allows /jobs**

### New Civil Engineer Careers (www.newcivilengineercareers.com)
- **Platform:** Drupal Epiq Jobs (server-rendered HTML)
- **Card selector:** `div.views-row article[id^="node-"]`
- **Pagination:** 0-indexed (standard Drupal `?page=N`)
- **Fields:** `h2.node__title a.recruiter-job-link` (title + URL), `div.description span.date`
  (absolute date "D Mon YYYY,"), `span.recruiter-company-profile-job-organization a` (employer),
  `div.location span` (location)

### Careers in Construction (www.careersinconstruction.com)
- **Platform:** Drupal Epiq Jobs — **identical HTML structure to NCE**
- Same selectors, same pagination, different domain

---

## Decisions

### D1: One adapter class, two internal parsers

`DrupalJobBoardAdapter` dispatches internally based on a `platform` config key:
- `platform = "jobiqo"` → `parse_jobiqo_html()` (Next.js `__NEXT_DATA__` extraction)
- `platform = "drupal_epiq"` → `parse_drupal_epiq_html()` (selectolax HTML parse)

**Rationale:** Keeps the adapter interface uniform (same CLI registration pattern) while
accurately handling platform differences. Alternative of separate classes would duplicate the
`fetch()` loop and pagination logic.

### D2: Pagination start points

- Jobiqo: start at `page=1` (not `page=0` — both return first batch; `page=2` is second).
- Drupal Epiq: start at `page=0` (standard Drupal convention).

### D3: contract_type_raw = None for all 3 sites

None of the 3 sites expose employment type in search-result cards. The pipeline's
`passes_contract()` filter handles downstream rejection of permanent roles based on title/
description signals. This is consistent with the ManpowerGroup adapter pattern.

### D4: salary_raw = None for Drupal Epiq sites (NCE, CIC)

Salary is not present in Drupal Epiq search cards. For Jobiqo (B4J), salary is extracted from
`salaryRange[0]["label"]` when present.

### D5: Jobiqo `duration` field ignored

`duration: 60` in B4J items is the job *posting duration* (60 days), not employment type.
No employment type label is available in search-result JSON without fetching each detail page.
Setting `contract_type_raw = None` is correct.

---

## Files Created / Modified

| File | Change |
|------|--------|
| `src/mechpm/adapters/drupal_jobboard.py` | New adapter (480 lines) |
| `tests/adapters/test_drupal_jobboard.py` | New tests (68 tests) |
| `tests/fixtures/adapters/building4jobs_page1.html` | Live fixture (B4J, page=1) |
| `tests/fixtures/adapters/nce_careers_page1.html` | Live fixture (NCE, page=0) |
| `tests/fixtures/adapters/careers_in_construction_page1.html` | Live fixture (CIC, page=0) |
| `config.toml` | Added 3 `[sources.*]` blocks |
| `src/mechpm/cli.py` | Import + registry + `_build_adapters` branch |

---

## Test Results

- **68 new tests** — all passing
- **877 total passing** (25 skipped) — no regressions

---

## Risks / Notes

- The `__NEXT_DATA__` JSON structure for Building4Jobs could change if Jobiqo upgrades
  their platform version. Key fields to monitor: `props.pageProps.data.jobs.pages`,
  `result_count`, and the `address`/`salaryRange` list types.
- NCE/CIC Drupal theme selectors are standard Epiq Jobs — stable, but theme updates could
  change them. The fallback selector `article.node-job` provides one layer of resilience.
- Building4Jobs `result_count=23` for "project manager" search — small dataset. This site
  will contribute fewer listings than NCE/CIC for PM-related queries.
