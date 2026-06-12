# Michael — History

## Project Seed (2026-06-12)
- **Project:** Mech-PM-Finder
- **Owner:** Steve
- **My role:** Backend / Scraping. I own source adapters, scheduling, and raw-listing persistence.
- **Mission:** Regularly scan multiple UK job boards for contract PM roles in mechanical engineering.
- **Tech stack:** TBD — wait for Tommy's decision.

## Learnings

### 2026-06-12: Source shortlist research

**Tier-1 picks and key fetch facts:**

| Source | Strategy | Key fact |
|---|---|---|
| Reed.co.uk | **Official JSON API** — free key via reed.co.uk/developers | Basic-auth (API key as username). `GET /api/1.0/search?keywords=project+manager+mechanical&locationName=UK&contract=true&resultsToTake=100`. Returns `salaryType` (per day / per hour / per annum) — critical for day-rate detection. Paginate with `resultsToSkip`. |
| Totaljobs | **HTML scrape** — robots.txt explicitly allows `/jobs/*?q=` | StepStone platform. URL: `https://www.totaljobs.com/jobs/project-manager/engineering-jobs?contract=true`. Also allows `/jobs/` path. `/JobSearch/RSS.aspx` is *disallowed* — no RSS. |
| CWJobs | **HTML scrape** — same StepStone platform as Totaljobs | robots.txt mirrors Totaljobs; `/jobs/*?q=` allowed. URL: `https://www.cwjobs.co.uk/jobs/project-manager/in-uk?contract=true`. Share adapter code with Totaljobs. |

**Hard decisions made:**
- **LinkedIn** — ToS robots.txt header explicitly says "use of automated means is strictly prohibited". Will NOT implement.
- **Indeed UK** — RSS format deprecated (404s confirmed in probe). Strong anti-bot (Cloudflare). robots.txt restricts. Deprioritized.
- **Hays.co.uk** — `Disallow: /jobs-search/` in robots.txt. Will not scrape.
- **IMechE** — `www.imeche.org` robots blocks all crawlers; `careers.imeche.org` resolves (200) and has no restrictive robots.txt — viable Tier-2 target.
- **Jobserve** — `/Job-Search.aspx` disallowed but modern `/gb/en/Job-Search/` returns 200. JS-heavy page likely needs Playwright. Tier-2.

**Reed API note:** robots.txt has `Disallow: /api/` for `*`, but this is the internal browser API path. The Developer API is an explicitly published, key-gated service — authorized use by registered API key holders is the intended use case; robots.txt directive is aimed at unauthorized scrapers, not registered API consumers.

### 2026-06-12: Vertical-specialist source addendum

**Tier-1 picks from vertical research (addendum to generalist shortlist):**

| Source | Strategy | Key fact |
|---|---|---|
| **RailwayPeople.com** | **HTML GET → parse `__NEXT_DATA__` JSON blob** — Next.js SSR (Jobiqo platform). URL: `https://www.railwaypeople.com/jobs?keywords=project+manager&jobtype=contract`. robots.txt: `Allow: /`, `Crawl-delay: 10`. | Meta description in HTML confirmed "308 Jobs" for PM+contract. No Playwright needed — listings embedded in `window.__NEXT_DATA__`. Enforce 10 s delay. |
| **Energy Jobline** | **HTML scrape** — Jobiqo/Drupal platform. URL: `https://www.energyjobline.com/jobs?keywords=project+manager+mechanical&location=United+Kingdom&contract_type=contract`. robots.txt: `Crawl-delay: 10`, disallows `/search/` (legacy path) and admin paths, but `/jobs?...` confirmed working. | Jobiqo platform — same as RailwayPeople. Possible shared adapter (different HTML structure from Next.js variant, but same query param conventions). Energy + O&G + renewables + nuclear all on one board. |
| **The Engineer Jobs** | **HTML scrape** — Cloudflare-managed robots.txt; `User-agent: *` `Allow: /`. URL: `https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract`. Confirmed search returns results. | Cloudflare blocks named AI bots (GPTBot etc.) but explicitly allows `User-agent: *`. Use polite User-Agent string. Mark Allen Group platform. |
| **Aviation Job Search** | **HTML scrape** — robots.txt allows all except `/api/*` and action paths. URL: `https://www.aviationjobsearch.com/en-GB/jobs?title=project+manager&job_categories=Engineering`. | Contract filter param: `contract_types=2`. Standard HTML, no JS rendering required. Confirm param values via manual probe before build. |

**Sectors with no specialist board recommended for Tier 1:**
- **Automotive** — PM contracts cluster on generalist boards + agencies. JustAutomotive exists but volume too low for MVP.
- **Maritime** — Very low UK mech-eng PM contract volume. Skip for MVP.
- **Construction/Civil specialist** — NCE Jobs, ICE Careers, IMechE Careers all low-volume. Covered sufficiently by Careerstructure (Tier 2) + generalist boards.

**Fetch gotchas by sector:**
- **Rail (RailwayPeople):** Next.js pages — page source includes `<script id="__NEXT_DATA__">` JSON blob. Parse this directly; much cleaner than CSS-selector scraping rendered HTML. Respect `Crawl-delay: 10`.
- **Energy (Energy Jobline):** robots.txt disallows `/search/` (the old Drupal search) but `/jobs?keywords=...` is the correct modern path and is NOT disallowed. Don't confuse the two.
- **Aerospace (Aviation Job Search):** `/api/*` is disallowed — do NOT try to call their internal API. Use the public search HTML page only.
- **Construction (Careerstructure):** StepStone platform (adapter reuse from Totaljobs/CWJobs). Complex robots.txt but no blanket `Disallow: /` for `User-agent: *`. The `Disallow: /*&page=*` rule blocks `&page=N` pagination — use `?page=N` (leading `?`) if pagination is needed, or reverse-sort and stop on seen IDs.
- **Cross-sector (The Engineer Jobs):** Cloudflare on robots.txt delivery. Assume same Cloudflare fronting on job pages — standard browser-like headers required. Confirm 200 response in adapter spike before committing.
- **Jobiqo platform (RailwayPeople + Energy Jobline):** Confirm whether both use identical `__NEXT_DATA__` structure. Energy Jobline appeared Drupal-based in probe — may be an older Jobiqo theme without Next.js. Treat as separate adapters until confirmed.

**Updated unified Tier-1 (MVP, 7 sources):**
Reed, Totaljobs, CWJobs, RailwayPeople, Energy Jobline, The Engineer Jobs, Aviation Job Search

**DefenceJobs.co.uk:** `User-agent: * Disallow: /` — fully hostile. Do not implement.

---

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: implement 7-source adapters per Tommy's contract. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.
