# Mech-PM-Finder

Scans UK job boards for **contract Project Manager roles in mechanical engineering**, deduplicates results, and produces a weekly Markdown report.

Runs automatically every Friday at 17:00 BST via Windows Task Scheduler.

---

## Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Copy the environment template and fill in your API keys:

```powershell
copy .env.example .env
# Open .env in an editor and set REED_API_KEY (and optionally OPENAI_API_KEY)
```

### Get a Reed API key

Register at <https://www.reed.co.uk/developers> to obtain a free API key, then add it to `.env`:

```
REED_API_KEY=your_key_here
```

---

## Run manually

```powershell
# Run all enabled sources
python -m mechpm.cli run-all

# Run a single source
python -m mechpm.cli run-all --source reed
```

Or use the batch wrapper (activates venv automatically):

```powershell
.\run.bat
.\run.bat --source reed
```

---

## Schedule via Windows Task Scheduler

```powershell
schtasks /create /tn "MechPM-Weekly" /tr "C:\Users\stevenn\mech-pm-finder\run.bat" /sc weekly /d FRI /st 17:00
```

To verify:

```powershell
schtasks /query /tn "MechPM-Weekly" /fo LIST
```

---

## Output

| Path | Contents |
|---|---|
| `data/raw/{YYYY-MM-DD}/{source}.jsonl` | Raw listings persisted by the orchestrator (one JSON object per line) |
| `reports/{YYYY-MM-DD}.md` | Weekly deduplicated Markdown report (produced by Polly's reporter) |

---

## Source coverage (MVP — 2026-06-12)

| Source | Strategy | Status |
|---|---|---|
| Reed.co.uk | Official JSON API | ✅ Active |
| Totaljobs | HTML scrape (StepStone) | 🔜 Next sprint |
| CWJobs | HTML scrape (StepStone) | 🔜 Next sprint |
| RailwayPeople | Next.js SSR (`__NEXT_DATA__`) | 🔜 Next sprint |
| Energy Jobline | HTML scrape (Jobiqo/Drupal) | 🔜 Next sprint |
| The Engineer Jobs | HTML scrape (Cloudflare-fronted) | 🔜 Next sprint |
| Aviation Job Search | HTML scrape | 🔜 Next sprint |

---

## Architecture

```
Scheduler
  └─ run.bat
       └─ python -m mechpm.cli run-all
            └─ Orchestrator (sequential)
                 ├─ ReedAdapter.fetch()  → data/raw/{date}/reed.jsonl
                 ├─ (future adapters…)
                 └─ Extractor (Ada) → Reporter (Polly) → reports/{date}.md
```

Config lives in `config.toml`. Secrets live in `.env` (never committed).

---

## Development

```powershell
# Install with browser extras (Playwright, for JS-heavy sources)
pip install -e ".[browser]"
playwright install chromium

# Reed adapter self-test
python -m mechpm.adapters.reed
```

Logs are written to stdout. Set `PYTHONUNBUFFERED=1` in the Task Scheduler action if output is buffered.
