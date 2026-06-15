# Decision: GitHub Action for Weekly Cron + Pages Deploy

**By:** Tommy (Lead)
**Date:** 2026-06-15
**Status:** Implemented

## Context

Steve wants the agent usable by non-technical PMs who don't want to install Python. The chosen approach: fork the repo, add API keys as repo secrets, GitHub Action runs weekly on cron, HTML report published to GitHub Pages. A non-technical user gets a bookmarkable URL to a refreshed report every Friday.

## Decisions

### 1. Cron Schedule
`0 16 * * 5` — Friday 16:00 UTC = 17:00 BST during British Summer Time.

### 2. DB Persistence: Option A — Commit to Repo
The `data/mechpm.sqlite` file is committed back to the repo after each run. Rationale:
- Simplest approach; binary diffs are small for SQLite (~10-50KB per week)
- Works perfectly with the fork model (each fork has its own copy)
- GitHub Actions cache (Option B) is evictable after 7 days of inactivity — fragile for weekly cadence
- Artefact upload/download (Option C) is clunky and expires after 90 days

### 3. Pages Source: GitHub Actions Workflow Deploy
Using the modern `actions/deploy-pages@v4` flow. No extra `gh-pages` branch needed. The `reports/` directory is uploaded as a Pages artifact and deployed directly.

### 4. Reports Archive Structure
- `reports/index.html` — lists all historical reports (newest first) with "Latest" badge
- `reports/latest.html` — meta-redirect to the most recent dated report (stable bookmark URL)
- `reports/{YYYY-MM-DD}.html` — individual dated reports
- Index is regenerated every run via `python -m mechpm.reporter.index_render`

### 5. Required Secrets
Users must set exactly 3 secrets in Settings → Secrets and variables → Actions → New repository secret:
- `REED_API_KEY` (free: https://www.reed.co.uk/developers)
- `ADZUNA_APP_ID` (free: https://developer.adzuna.com/)
- `ADZUNA_APP_KEY` (same registration)

`OPENAI_API_KEY` is set to empty string in the workflow env — LLM extraction is optional.

### 6. Workflow Permissions
Explicitly set:
- `contents: write` — commit reports + DB back to main
- `pages: write` — deploy to GitHub Pages
- `id-token: write` — required by `actions/deploy-pages`

### 7. Failure Handling
- `continue-on-error: true` on the pipeline step — if a source fails (API quota, network), the pipeline still produces a partial report
- Tests (`pytest`) are NOT continue-on-error — broken code must not ship
- The pipeline already tolerates per-source failures internally (adapter errors are logged, not raised)

### 8. Manual Trigger
`workflow_dispatch:` is included so users can force-run at any time via the Actions tab.

## Files Delivered
- `.github/workflows/weekly.yml` — workflow definition
- `src/mechpm/reporter/index_render.py` — index page generator (Python module)
- `tests/test_index_render.py` — 13 tests for the index generator
- `.env.example` — restored with current var list
- `README.md` — full rewrite for hosted-first usage

## Verification
- YAML validated via `yaml.safe_load()`
- Index generator produces correct output locally
- 13 new tests pass; 425 total tests pass (0 regressions)
