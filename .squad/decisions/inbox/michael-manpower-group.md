# Decision: Manpower Group Adapter — HTML Scrape Strategy

**Date:** 2026-06-17
**Author:** Michael (backend)
**Source:** careers.manpowergroup.co.uk

## Context

Manpower Group operates a public job search at `careers.manpowergroup.co.uk/jobs`. Live
probe (2026-06-17) confirmed:
- 200 OK responses with full HTML job listings
- Cloudflare in CDN-only mode (not blocking)
- Standard server-rendered HTML (no SPA, no JS-injected content)
- robots.txt allows `/jobs` — only `/admin/*`, `/sa/*`, `/api/*` are disallowed
- 6 listings per page; pagination via `&page=N` (1-indexed, page 1 = no param)

## Decision

**Strategy: direct HTML scrape** using `selectolax` + `httpx`, consistent with the
michael_page and energy_jobline adapters.

**No API used** — `/api/*` is explicitly blocked in robots.txt.

## DOM Contract (confirmed 2026-06-17)

| Field | Selector |
|---|---|
| Card container | `li.job-result-item` |
| Title + URL | `div.job-title a` (href is site-relative) |
| Listing ID | numeric suffix of URL slug via `r'-(\d+)$'` |
| Location | `li.results-job-location` text |
| Salary | `li.results-salary` text (textual, not numeric) |
| Posted date | `li.results-posted-at` (relative: "Posted N days ago") |
| Description | `p.job-description` (short snippet) |
| Agency | `figure.recruiter-figure img[alt]` |
| Contract type | **not exposed** in search cards |

## Key Design Choices

1. **`contract_type_raw = None`** — the search results page does not expose contract type.
   The extractor's `parse_contract_type(None, title, description)` scans the description
   snippet; ambiguous cases default to "contract" per the conservative extractor policy.

2. **Relative date parsing** — `_parse_posted_at()` converts "Posted 12 days ago" →
   `datetime.now(UTC) - timedelta(days=12)`. Approximate but sufficient for `since` filtering.

3. **Pagination stops on missing next-page link** — `a[rel=next]` is the sentinel.

4. **No contract-type URL filter** — the site has no `contract_type=` param. All job types
   are returned; the pipeline's `passes_contract()` filter handles rejection.

## Observed Behaviour (live smoke test 2026-06-17)

- Queries returning results: "project manager engineering" (2 pages, 11 total), "project director" (1 page)
- Queries returning 0 results: "project engineer mechanical", "document controller", "assurance engineer"
- All 11 fetched listings were internal Experis/ManpowerGroup recruiter/BD roles (rejected by PM+mech filters)
- Stored after full pipeline: 0 from this source

## Note on Coverage

Manpower Group's public careers site primarily posts internal staffing/recruiting positions
(Experis brand). This is a known characteristic of the source. The adapter is correct;
the low stored count reflects the source's content, not a bug.
