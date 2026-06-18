# Skill: Multi-Source Keyword Slate Design

**When to use:** Designing search query strategies for multi-source job/listing aggregators where each source has different operator support (AND-only, OR, exclude, sitemap-based, no-query).

## Pattern

### Step 1 — Map operator capabilities per source
For each source, determine:
- Does it support OR? (e.g., Adzuna `what_or`)
- Does it support exclusions? (e.g., Adzuna `what_exclude`)
- Is it AND-only? (e.g., Reed — single keyword string)
- Is it keyword-less? (e.g., sitemap-based, vertical board)

### Step 2 — Choose strategy per capability tier

| Capability | Strategy |
|-----------|----------|
| Native OR + exclude | Single broad query with exclusions at source level |
| AND-only, no OR | Multiple sequential queries (≤4-5), union results by source ID |
| Keyword-less (vertical board) | No keyword change; site IS the filter |
| Sitemap/URL-pattern | Broaden URL filter patterns; control via safety cap |

### Step 3 — Design the keyword slate
- Start with the core term (e.g., "project manager mechanical")
- Add sector-adjacent expansions (e.g., "HVAC", "M&E", "building services")
- For OR-capable sources: combine into one broad query
- For AND-only sources: one query per sector expansion

### Step 4 — Define the filter taxonomy as safety net
Broader queries require a stronger downstream filter. Define:
- **Positive list** (terms that indicate relevance): title regex + body signals + domain keywords
- **Negative list** (terms that disqualify): word-boundary matched phrases
- **Scoring balance**: title weight > description weight; mech_score must beat disqualify_score

### Step 5 — Set caps and politeness constraints
- Per-source page/query caps (e.g., ≤4 queries × 1 page for rate-limited APIs)
- Safety caps on total listings (e.g., 500 per source)
- Crawl-delay between queries (respect robots.txt)
- Estimate total runtime and ensure it's acceptable

### Step 6 — Estimate yield and set acceptance criteria
- Estimate per-source fetched/stored counts
- Account for cross-source overlap (dedup removes ~20-30% typically)
- Set a floor (e.g., "≥40 stored after dedup") as the primary acceptance criterion
- Set a precision floor (e.g., "≥80% of stored are genuine") as secondary

## Anti-patterns
- Don't broaden queries without strengthening the negative filter
- Don't combine sector-specific terms with AND (kills results — "HVAC AND mechanical" is too narrow)
- Don't assume OR when the source uses AND — test first
- Don't exceed 5 queries per AND-only source (runtime + rate limits)
- Don't lower dedup threshold just because you expect more overlap — validate first
