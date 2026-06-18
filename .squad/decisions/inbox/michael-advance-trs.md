# Decision: Advance TRS Adapter — WP Job Manager REST API

**Date:** 2026-06-18  
**Author:** Michael (backend/scraping)  
**Status:** Shipped

## Context

Advance TRS (www.advance-trs.com) is a UK engineering/rail recruitment agency. The site
runs WordPress with the WP Job Manager plugin, which exposes a standard REST API at
`/wp-json/wp/v2/job-listings`. There are currently ~102 live contract roles.

## Decisions

### 1. No keyword search — fetch all contract roles

The WP Job Manager REST API does not support keyword/text search. The only useful filter
is `?job-types=7` (Contract). We fetch all ~102 contract jobs (2 pages at per_page=100)
and rely on the downstream pipeline `passes_contract()` + keyword filters to reduce noise.

**Rejected alternative:** Scraping the HTML search results page. The REST API is cleaner,
fully structured, and already returns all the fields we need.

### 2. Pagination via `x-wp-totalpages` header

The total page count is in the `x-wp-totalpages` response header (not in the body). We
read it from page 1 and loop from page 2 onwards. Default to 1 if the header is absent.

### 3. `salary_raw` is almost always empty

The `meta._job_salary` field is blank for most listings. Rates likely appear in the HTML
description (`content.rendered`). We store `salary_raw = None` when blank and pass the
full description HTML through as `description_raw` for the extractor to parse.

### 4. `contract_type_raw` derived from `job-types` taxonomy

The API returns `job-types: [7]` for contract roles. We map this to `contract_type_raw =
"Contract"`. This gives the pipeline a reliable pre-filter signal without needing
description parsing.

### 5. Config has no `keywords_list`

Unlike keyword-driven adapters (Reed, Phenom, etc.), `advance_trs` has no `keywords_list`
in `config.toml` because the adapter fetches everything. The `SourceConfig` model's
`_normalise_keywords` validator handles the empty list gracefully (falls back to default
scalar keyword, which the adapter ignores via `**kwargs`).

## Files Changed

- `src/mechpm/adapters/advance_trs.py` — new adapter
- `tests/adapters/test_advance_trs.py` — 46 new tests (all passing)
- `config.toml` — `[sources.advance_trs]` entry added (enabled = true)
- `src/mechpm/cli.py` — import + registry + build branch added

## Test Suite

809 total passing, 25 skipped — no regressions.
