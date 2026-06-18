# Decision: Turner & Townsend Adapter

**Date:** 2026-06-18
**By:** Michael (Backend / Scraping)
**Status:** Implemented

## What

Shipped `TurnerTownsendAdapter` at `src/mechpm/adapters/turner_townsend.py`.

**API:** POST `https://www.turnerandtownsend.com/api/careers/searchvacancies` (SmartRecruiters proxy).

**Request payload** (per keyword):
```json
{ "page": 1, "pageSize": 50, "query": "project manager", "countries": ["United Kingdom"] }
```

**Key decisions:**

1. **Country filter must be `"United Kingdom"`** — T&T's API does not accept ISO codes ("gb", "uk"). Discovered during coordinator recon.

2. **Listing ID from ref URL tail** — each `content[]` entry's `ref` field is a public SmartRecruiters API URL ending in a numeric ID (`/postings/744000131433769`). We extract the numeric tail as `source_listing_id`.

3. **URL strategy** — pre-enrichment, `url` is set to the SmartRecruiters `ref` URL (API endpoint, not human-readable). If enrichment is enabled and `postingUrl` is returned, it is replaced with the human-readable careers page URL.

4. **Enrichment is opt-in** (`enrich_detail = false` in config.toml). Enrichment GETs each listing's `ref` URL (Accept: application/json) to populate `contract_type_raw` (from `typeOfEmployment.label`) and `description_raw` (from `jobAd.sections.jobDescription.text`). Adds ~1 s per listing; disabled by default to keep run time predictable.

5. **No contract-type filtering at adapter level** — T&T mixes full-time, FTC, and contract roles. The pipeline's `passes_contract()` handles filtering. Most T&T roles appear as "Full-time" even when they are fixed-term; title-level evidence of FTC is preserved.

6. **robots.txt compliance** — T&T robots.txt is permissive (only `/umbraco/`, `/umbraco-preview/`, and authentication paths are blocked). No `Crawl-delay` directive; we use `_PAGE_DELAY_SECONDS = 3` between page requests to be polite.

## Config entry

```toml
[sources.turner_townsend]
enabled = true
crawl_delay = 3
max_pages_per_query = 5
enrich_detail = false
keywords_list = [
    "project manager",
    "project director",
    "construction manager",
    "commissioning manager",
    "project engineer",
    "programme manager",
    "contracts manager",
]
```

## Test coverage

74 tests in `tests/adapters/test_turner_townsend.py`:
- Helper function unit tests (ID extraction, date parsing, location, custom fields)
- Field mapping (`_content_item_to_raw_listing`)
- Detail enrichment (`_apply_detail`, including immutability check)
- Adapter constructor and defaults
- `fetch()`: single page, multi-page pagination, `max_pages_per_query` cap,
  empty-page early exit, HTTP error, `since` filter
- Dedup: across keywords, across pages
- Enrichment: contract type, description, URL replacement
- Enrichment failure handling: 404 → unchanged, timeout → unchanged
