# Decision: Aviation Job Search — Sitemap + LD+JSON Strategy

**Date:** 2026-06-15  
**Author:** Michael (Backend / Scraping)  
**Status:** Implemented

---

## Decision

Replaced the original CSS-selector-based HTML scraping strategy for Aviation Job Search with a **sitemap-driven + schema.org/JobPosting LD+JSON parsing** approach.

**Fetch strategy:**
1. Fetch `https://www.aviationjobsearch.com/en-GB/sitemap/jobs.xml` (explicitly listed in robots.txt)
2. Filter job URLs by `/management/` category path or PM-keyword title slug
3. Fetch each matched individual job-detail page (SSR; 3 s crawl-delay)
4. Extract `{"@type": "JobPosting"}` LD+JSON from each page
5. Map structured JSON to `RawListing`

**Robots.txt compliance:** The `/api/v1/jobs` endpoint (the internal AJAX API) is `Disallow`-ed. The sitemap path and `/jobs/*` detail pages are both allowed. This strategy is fully compliant.

---

## Why

**Original strategy failed because:**
- The search results page is a client-side app (`AppData.is_ssr = false`). All listings are loaded via an AJAX call to `/api/v1/jobs` after the page loads.
- The `#searchResults` div is empty in the static HTML — CSS selectors will never match.
- The `/api/v1/jobs` endpoint is explicitly `Disallow`-ed in robots.txt.

**Alternative considered — Playwright headless:**
- Would render the AJAX-loaded results but adds a heavy browser dependency.
- `playwright` is already in the `[browser]` optional extra but is currently only approved for The Engineer Jobs.
- Not necessary here: the sitemap + LD+JSON approach is cleaner and fully headless-HTTP.

**Alternative considered — Disable the adapter:**
- Would lose the only specialist aviation aerospace engineering board in the MVP.
- Not needed: the sitemap approach works.

**Why LD+JSON (not HTML parsing of job-detail pages):**
- LD+JSON is structured, machine-readable, and schema.org-standardised.
- Less brittle than CSS selectors: the structured data layer changes far less often than HTML templates.
- All required fields are present: title, employer, location, datePosted, employmentType, description.

**Brotli gotcha found and fixed:**
- `Accept-Encoding: gzip, deflate, br` in the original browser headers caused the server to respond with Brotli compression.
- httpx has no built-in Brotli decoder. The compressed sitemap (60KB → 7KB) was received as garbled bytes → 0 regex matches → 0 listings.
- Fixed by removing `br` from `Accept-Encoding`. Now uses `gzip, deflate` only.
- **General rule logged in history:** Never advertise `br` with httpx unless `brotli` package is installed.

---

## Impact

- **+6 listings per run** from the aviation management/PM category (current universe; will vary with live job count)
- **robots.txt: fully compliant** — no API calls, no JS rendering bypass
- **Adapter volume is small by design** — Aviation Job Search is a specialist niche board; 297 total active listings, ~6 management roles. This is correct and expected.
- **Ada's filter note:** `employmentType` is `"FULL_TIME"` for all current management listings — this board does not prominently advertise contract roles. Ada's extractor should mine `description_raw` for contract/day-rate signals rather than relying on `contract_type_raw`.
- **Test regression:** 181 passed, 25 skipped — no regressions.
