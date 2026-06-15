# Decision: RailwayPeople Adapter Full-Field Calibration

**Date:** 2026-06-14  
**By:** Michael (Backend / Scraping)  
**Status:** Implemented  

## Problem

Sprint-2 RailwayPeople adapter fetched 50 listings but only `title` was populated.
`employer`, `location`, `source_url`, `posted_at`, and rate fields were all null.

## Root Cause

The Jobiqo `__NEXT_DATA__` JSON schema uses non-standard field names that the
original `_map_job_to_raw_listing()` did not handle:

| Expected key (old code) | Actual key (Jobiqo schema) | Notes |
|---|---|---|
| `company` / `employer` | `organization` | flat string, not nested |
| `location` / `locationName` | `address` | **list** of "City, Country" strings |
| bare string URL fields | `url` | nested dict `{"__typename":"Url","path":"/job/slug-id"}` |
| `urlNoPrefix` | `urlNoPrefix` | flat relative path — added as fast-path |
| `datePosted` / `createdAt` | `published` | ISO-8601 with TZ offset |
| `salary` / `rate` | `salaryRangeFree` | nested dict; usually null in search results |

The `_find_jobs_list()` discovery logic **correctly** found the list at
`props.pageProps.data.jobs.pages` (because `"url"` key exists in the dict, even
as a nested object).  The failure was entirely in the mapping step.

## Changes Made

- `src/mechpm/adapters/railwaypeople.py`:
  - `_extract_url()`: prioritises `urlNoPrefix` (flat string), then `url.path` (nested dict), then falls back to plain string URL fields.
  - `_map_job_to_raw_listing()`: rewrites employer → `organization`, location → `address` list joined with `"; "`, salary → `salaryRangeFree.minSalary/maxSalary`, posted_at → `published`, contract_type_raw → `"Contract"` default.
  - `_MAPPED_KEYS` updated to include new Jobiqo field names so they don't spill into `metadata`.

## Fields That Now Extract Cleanly

| Field | JSON path | Population rate (50 listings) |
|---|---|---|
| `title` | `title` | 100% |
| `employer` | `organization` | 100% |
| `location_raw` | `address` (list, joined) | 100% |
| `url` | `urlNoPrefix` | 100% |
| `source_listing_id` | `id` (int → str) | 100% |
| `posted_at` | `published` (ISO-8601 + TZ) | 100% |
| `contract_type_raw` | default `"Contract"` | 100% |

## Fields Not Available in Search Results

- `description_raw`: not in listing-level JSON; only on individual job detail pages. Left `None`.
- `salary_raw`: `salaryRangeFree` is always null in these search results (RailwayPeople contract listings rarely publish rates publicly). Infrastructure exists to extract it when present.

## Live Verification

- 50 fetched / 50 extracted / 0 quarantined / 47 filtered_out / 3 stored
- All 3 survivors have employer + location + source_url correctly populated
- Population rates: title 100%, employer 100%, location 100%, url 100%, posted_at 100%

## Tests

- `tests/adapters/test_railwaypeople.py` (11 new tests)
- `tests/fixtures/adapters/railwaypeople_page1.json` (10-job snapshot)
- Full suite: 106 passed, 25 skipped, 0 failed (baseline was 95 passed)
