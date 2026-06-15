# Mech-PM-Finder

Scans UK job boards for **contract Project Manager roles in mechanical engineering**, deduplicates across sources, and produces a weekly HTML report with structured details: title, employer/agency, location, day rate, duration, IR35 status, and start date.

Built for non-technical PMs who want a bookmarkable URL refreshed every Friday — no local install required.

---

## Use it without installing anything (recommended)

Fork this repo and let GitHub Actions run the pipeline for you every Friday. You get a hosted report at `https://<your-username>.github.io/Eng_Job_finder/latest.html`.

1. **Fork the repo** — click the "Fork" button at the top-right of this page.
2. **Enable GitHub Actions** — go to your fork's **Actions** tab → click "I understand my workflows, go ahead and enable them".
3. **Enable GitHub Pages** — go to **Settings → Pages → Source** → select **GitHub Actions** from the dropdown → Save.
4. **Add your API keys as repository secrets** — go to **Settings → Secrets and variables → Actions → New repository secret** and add these three:

   | Secret name | Where to get it |
   |---|---|
   | `REED_API_KEY` | Free — register at <https://www.reed.co.uk/developers> |
   | `ADZUNA_APP_ID` | Free (1000 calls/day) — register at <https://developer.adzuna.com/> |
   | `ADZUNA_APP_KEY` | Same Adzuna registration |

5. **Trigger the first run** — go to **Actions → Weekly Report → Run workflow** (button on the right) → click "Run workflow". Wait ~5 minutes.

Your report will be live at `https://<your-username>.github.io/Eng_Job_finder/latest.html`. Bookmark it — it refreshes automatically every Friday at 17:00 BST.

---

## Run it locally

If you prefer to run on your own machine:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -e .
cp .env.example .env          # then fill in your API keys
python -m mechpm.cli run-all
```

Use the GitHub Action (recommended) or your OS scheduler if running locally.

---

## Output

| Path | Contents |
|---|---|
| `reports/latest.html` | Always points to the most recent report |
| `reports/{YYYY-MM-DD}.html` | Weekly HTML report (self-contained, styled) |
| `reports/{YYYY-MM-DD}.md` | Same report in Markdown |
| `reports/index.html` | Archive page listing all historical reports |
| `data/mechpm.sqlite` | Dedup database (persisted between runs) |

Hosted version: `https://<your-username>.github.io/Eng_Job_finder/` (index) or `/latest.html` (direct).

---

## Source coverage

| Source | Strategy | Status |
|---|---|---|
| Reed.co.uk | Official JSON API | ✅ Active |
| RailwayPeople | Next.js SSR (`__NEXT_DATA__`) | ✅ Active |
| Adzuna | REST API | ✅ Active |
| Energy Jobline | HTML scrape (Jobiqo/Drupal) | ✅ Active |
| Aviation Job Search | Sitemap-driven HTML | ✅ Active |
| Totaljobs | HTML scrape (StepStone) | ⏸️ Disabled (Akamai bot protection) |
| CWJobs | HTML scrape (StepStone) | ⏸️ Disabled (Akamai bot protection) |
| The Engineer Jobs | HTML scrape | ⏸️ Disabled (Cloudflare challenge) |

See `.squad/decisions.md` for rationale on disabled sources.

---

## Architecture

```
GitHub Actions (cron: Friday 16:00 UTC)
  └─ python -m mechpm.cli run-all
       └─ Orchestrator (sequential, per-source failure isolation)
            ├─ ReedAdapter       → data/raw/{date}/reed.jsonl
            ├─ AdzunaAdapter     → data/raw/{date}/adzuna.jsonl
            ├─ RailwayPeople     → data/raw/{date}/railwaypeople.jsonl
            ├─ EnergyJobline     → data/raw/{date}/energy_jobline.jsonl
            ├─ AviationJobSearch → data/raw/{date}/aviation_job_search.jsonl
            └─ Pipeline: Extract → Filter → Dedup → Store → Report
                 ├─ reports/{date}.html + .md
                 └─ data/mechpm.sqlite (cumulative)
```

Config: `config.toml`. Secrets: `.env` (local) or GitHub repo secrets (hosted).

---

## API keys

| Key | Source | Cost |
|---|---|---|
| `REED_API_KEY` | <https://www.reed.co.uk/developers> | Free |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | <https://developer.adzuna.com/> | Free (1000 calls/day) |
| `OPENAI_API_KEY` (optional) | <https://platform.openai.com/> | Pay-as-you-go (only for LLM extraction fallback) |

---

## Development

```bash
pip install -e ".[browser]"   # includes Playwright for JS-heavy sources
python -m pytest tests/ -q    # run test suite
python -m mechpm.reporter.index_render  # regenerate index page locally
```
