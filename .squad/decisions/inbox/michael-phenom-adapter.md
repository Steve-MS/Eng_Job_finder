# Decision: Phenom Platform Adapter — BAM Careers + Mace Group

**By:** Michael (Backend / Scraping)  
**Date:** 2026-06-17  
**Status:** Shipped

## What

Single `PhenomAdapter` class in `src/mechpm/adapters/phenom.py` that handles any
Phenom People ATS-powered career site via config. Initial deployment covers two
construction/infrastructure employers:

| Source name  | Domain                    | Site path | Employer fallback |
|--------------|---------------------------|-----------|-------------------|
| `bam_careers` | `www.bamcareers.com`     | `/uk/en`  | BAM Construction  |
| `mace_group`  | `careers.macegroup.com`  | `/gb/en`  | Mace Group        |

## Discovery

**Live recon 2026-06-17:**

- Both sites confirmed Phenom platform via `phenomtrack.min.js` and
  `"widgetApiEndpoint": "https://{domain}/widgets"` in page config.
- **robots.txt**: permissive — only `/chatbot`, `/iauth`, `/socialAuth` disallowed.
  No `Crawl-delay` directive; 5 s inter-page delay applied as courtesy.
- **No bot protection** (no Cloudflare, no Akamai).

## Technical approach: HTML scrape + embedded JSON

The Phenom `/widgets` API endpoint returns the same full HTML page regardless
of path or HTTP method (POST/GET both return 554 KB HTML). The clean data path
is parsing the embedded `phApp.ddo` JSON block in the search-results HTML.

**Data location:**
```
phApp.ddo.eagerLoadRefineSearch.data.jobs[]    — 10 jobs per page
phApp.ddo.eagerLoadRefineSearch.totalHits      — total matching count
```

**Pagination:** `?keywords={kw}&from=N&s=1` (N = 0, 10, 20, ...)

**Job URL:** `https://{domain}/{site_path}/job/{jobSeqNo}`  
(Title slug is optional — site serves the page without it; confirmed live.)

**Key extraction notes:**
- `reqId` must be numeric — Mace HTML contains placeholder entries with
  `"reqId": "Required Id"` that are filtered out pre-listing creation.
- BAM cards include `companyName` per job (e.g. "BAM Nuttall", "BAM Façades").
  Mace cards have `companyName: null` — fallback to configured `employer_name`.
- `postedDate` format: `"2026-06-05T00:00:00.000+0000"` (ISO-8601 UTC with
  milliseconds and numeric offset). Parser handles `+0000`, `Z`, and plain date.
- `multi_location[]` array used for `location_raw`; falls back to `city + country`.

## Why not the widgets API?

Attempted POST/GET to various `/widgets/*` sub-paths; all returned the full
search-results HTML (same 554 KB response). The widgets API is a client-side
SPA framework — actual AJAX calls are made by the embedded JavaScript using
tokens not easily reproducible without a browser. HTML scraping of the embedded
JSON is simpler, faster, and more reliable.

## Results

- **BAM Careers:** 53 unique listings (4 keywords × up to 5 pages)
- **Mace Group:** 77 unique listings (4 keywords × up to 5 pages)
- **Full pipeline (8 sources):** 820 fetched, 60 stored after filtering + dedup

## Implications

- Any new Phenom-powered site can be added with a 4-line config.toml entry —
  no code changes required.
- International results (Mace is a global firm) pass through the adapter; the
  pipeline's `passes_uk()` filter handles rejection downstream.
- `max_pages_per_query = 5` cap prevents runaway fetches; can be increased
  per-source in config if deeper coverage is needed.
