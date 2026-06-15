### 2026-06-15: Disable Totaljobs and CWJobs — Akamai Bot Manager blocks live fetch

**By:** Michael (backend/scraping) — coordinator finalised after live probe
**Requested by:** Steve

**Decision:** Set `enabled = false` for both `[sources.totaljobs]` and `[sources.cwjobs]` in `config.toml`. Keep the calibrated adapter (`src/mechpm/adapters/stepstone.py`), the captured HTML fixtures, and all 16 fixture tests in place — they're correct and will work the day we find a clean fetch path.

**Why:**
- Live probe 2026-06-15 against both `https://www.totaljobs.com/jobs/project-manager/in-uk?contract=true` and the CWJobs equivalent returned **HTTP 200 with a stripped body (~98KB, 0 job cards)**.
- Response headers include `x-akamai-transformed: 9 - 0` and a `Set-Cookie: _abck=...~-1~...` token. The `~-1~` flag in the `_abck` cookie is Akamai Bot Manager's "request not yet validated as human" marker.
- Body at offset 30000 is base64-encoded SVG (a branded splash page Akamai serves to unvalidated clients), not real listing HTML.
- Bypassing Akamai requires either TLS-fingerprint spoofing (curl-impersonate) or a headless browser with bot-detection evasion plugins. Both qualify as ToS-bypass under Michael's charter: *"must NOT bypass site ToS or scrape behind authentication walls that prohibit it."*
- This is the **second** anti-bot block in the same sprint (The Engineer is Cloudflare-blocked, also disabled).

**What still works:**
- Fixture-based unit tests pass 16/16 — the parser is correct against the captured HTML.
- Selectors are documented in the adapter docstring for future use:
  - Card root: `article[data-at="job-item"]`
  - Title/link: `a[data-at="job-item-title"]`
  - Company: `[data-at="job-item-company-name"]`
  - Location: `[data-at="job-item-location"]`
  - Salary: `[data-at="job-item-salary-info"]`
  - Date: `[data-at="job-item-timeago"] > time`
- The working search-URL discovery is documented: `/jobs/project-manager/in-uk?contract=true` (replaces the stale `/jobs/project-manager/engineering-jobs` which returned HTTP 500).
- All findings preserved in the adapter docstring + test suite.

**Impact:**
- -2 sources from the active fetch loop. After this sprint the live source roster is: Reed, RailwayPeople, Energy Jobline (newly calibrated), + Aviation Job Search (Michael-12 in progress).
- No quality regression in current reports — Reed + RailwayPeople were already producing the bulk of relevant listings.
- Calibration work is not lost — fixtures and tests stay as future-proof reference material.

**Alternatives for Tommy to evaluate (priority order):**

1. **RSS / job-feed discovery (15-min recon — recommended first):** Check `https://www.totaljobs.com/rss`, `/feed`, `/jobs.rss`, `/atom`. Many job boards publish a public feed unprotected by Akamai. If found, write a simpler `stepstone_rss.py` adapter — feeds typically yield 50-200 recent listings and have stable schemas.

2. **JobServe / Adzuna aggregators (1-hr eval):** Totaljobs syndicates many of its listings into aggregator feeds. JobServe and Adzuna both have public APIs that include Totaljobs/CWJobs content. Cost: free tier on Adzuna, JobServe needs an account. Lower fidelity (no salary on free tier) but bypasses Akamai entirely.

3. **Playwright with `--headed` + manual cookie warming (low priority):** Spawning a real browser, navigating manually once to acquire a validated `_abck` cookie, then injecting it into httpx requests. Brittle (cookie expires), violates ToS spirit even if technically not a bypass. Not recommended.

4. **Drop StepStone family entirely (acceptable):** Energy Jobline and Aviation Job Search likely cover most of the same listings via different routes. If RSS recon and Adzuna both fail, this is the right call.

**Files touched:**
- `config.toml` — set `enabled = false` for totaljobs and cwjobs with explanatory comment
- `src/mechpm/adapters/stepstone.py` — calibrated parser preserved (16/16 tests pass)
- `tests/adapters/test_stepstone.py` — fixture tests preserved
- `tests/fixtures/adapters/totaljobs_page1.html`, `cwjobs_page1.html` — captured 2026-06-15
- `.squad/skills/akamai-bot-manager-detection/SKILL.md` — new reusable detection pattern
