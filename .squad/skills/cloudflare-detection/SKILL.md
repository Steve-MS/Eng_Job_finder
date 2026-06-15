# SKILL: Cloudflare Anti-Bot Detection

**Owner:** Michael (Backend / Scraping)  
**Created:** 2026-06-15  
**Last-updated:** 2026-06-15  
**Status:** Verified against live target (jobs.theengineer.co.uk)

---

## Purpose

Determine, before any selector or parser work, whether a target job board is behind Cloudflare anti-bot protection. Stops calibration early and prevents wasted effort on bypassing Cloudflare (which violates Michael's charter).

---

## When to Use

Run this skill at the start of every new adapter calibration for any site that:
- Is hosted on Cloudflare (check with `dig <hostname>` or `nslookup` — Cloudflare IP ranges are `104.x.x.x`, `172.64-71.x.x`, `198.41.x.x`)
- Previously returned HTTP 403 during ad-hoc probing
- History notes indicate "Cloudflare-fronted" or "Cloudflare managed robots.txt"
- Is a trade publication, niche job board, or media-company jobs portal (common Cloudflare users)

---

## Detection Protocol

### Step 1 — Probe with realistic browser headers (PowerShell)

```powershell
Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.AllowAutoRedirect = $true
$client = New-Object System.Net.Http.HttpClient($handler)

$client.DefaultRequestHeaders.TryAddWithoutValidation(
    "User-Agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
) | Out-Null
$client.DefaultRequestHeaders.TryAddWithoutValidation("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8") | Out-Null
$client.DefaultRequestHeaders.TryAddWithoutValidation("Accept-Language", "en-GB,en;q=0.9") | Out-Null

$response = $client.GetAsync("https://TARGET_URL").Result
$body     = $response.Content.ReadAsStringAsync().Result
$status   = [int]$response.StatusCode

$serverHeader = if ($response.Headers.Contains("Server")) { ($response.Headers.GetValues("Server") -join ", ") } else { "" }
$cfRay        = if ($response.Headers.Contains("CF-RAY"))  { ($response.Headers.GetValues("CF-RAY")  -join ", ") } else { "" }

Write-Host "Status: $status | Server: $serverHeader | CF-RAY: $cfRay"
$client.Dispose()
```

> **Important:** Use `System.Net.Http.HttpClient` — NOT `Invoke-WebRequest`. `Invoke-WebRequest` throws on 403/503 before you can read the body, losing the evidence you need.

### Step 2 — Classify the response

| Condition | Classification | Action |
|---|---|---|
| HTTP 403 + `Server: cloudflare` + `CF-RAY` header present | **Hard block** — Cloudflare WAF rule actively blocking this IP/UA | Stop. Disable adapter. Decision drop. |
| HTTP 403 + `Server: cloudflare` but no `CF-RAY` | **Edge block** — IP-level or rate-limit block | Stop. Disable adapter. May be transient; re-probe in 24h. |
| HTTP 200 + body contains any JS challenge marker (see table below) | **Passive JS challenge** — Cloudflare tunnelled through 200 | Stop. Disable adapter. Phase B (Playwright) may or may not help. |
| HTTP 200 + `Server: cloudflare` but no JS challenge markers | **Cloudflare CDN only** — no active bot protection | Proceed with calibration. Cloudflare is transparent here. |
| HTTP 200 + no Cloudflare headers | **No Cloudflare** | Proceed with calibration. |
| HTTP 503 + `Server: cloudflare` + body contains JS challenge markers | **JS challenge (BotFight)** | Stop. Phase B (Playwright) may help — check with Tommy first. |

### Cloudflare JS challenge markers (check body for any of these)

```python
_CF_CHALLENGE_MARKERS = (
    "Just a moment...",           # Standard CF JS challenge title
    "cf-browser-verification",    # Old CF element ID
    "_cf_chl_",                   # CF challenge token prefix
    "Attention Required! | Cloudflare",  # CF block page title
    "cf-challenge-platform",      # Modern CF Turnstile element
    "Checking your browser",      # Visible challenge text (some locales)
)
```

---

## Decision Rules

### BLOCKED — Stop Calibration

If **any** of the following are true, the source is blocked by Cloudflare:

1. HTTP 403 with `Server: cloudflare`
2. HTTP 503 with `Server: cloudflare`
3. HTTP 200 but body contains any `_CF_CHALLENGE_MARKERS` entry

**Actions when blocked:**
1. Save raw response to `tests/fixtures/adapters/<source>_recon.html`
2. Set `enabled = false` in `config.toml` with comment: `# Cloudflare-protected — disabled YYYY-MM-DD pending alternative strategy`
3. Write decision drop to `.squad/decisions/inbox/michael-<source>-blocked.md` (see template below)
4. Append learning to `.squad/agents/michael/history.md`
5. **Do NOT attempt bypass.** Charter prohibits this.

### NOT BLOCKED — Proceed

If HTTP 200 and no challenge markers, and `Server: cloudflare` is just CDN passthrough:

1. Save response body to `tests/fixtures/adapters/<source>_page1.html` (or `.json`)
2. Continue with normal calibration workflow

---

## Alternative Strategies Template (for decision drop)

When writing a decision drop for a blocked source, always include these alternatives for Tommy:

| Option | Description | Risk |
|---|---|---|
| **RSS feed** | Probe `/rss`, `/feed`, `/rss.xml`, `/jobs/rss` — RSS typically bypasses CF bot protection | Low — highly recommended first step |
| **Email alerts** | Register dedicated inbox + IMAP/email adapter | Low — ToS-friendly; daily latency |
| **Playwright stealth** | Patched headless Chromium (`undetected-playwright`) | High — fragile, arms-race maintenance, ethically grey |
| **Commercial data feed** | Contact publisher for aggregator XML feed | Low risk, higher cost/effort |
| **Drop source** | Remove from MVP, revisit v0.2 | Zero risk |

---

## Real-World Example: jobs.theengineer.co.uk (2026-06-15)

```
GET https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract
User-Agent: Mozilla/5.0 ... Chrome/131.0.0.0 Safari/537.36

Response:
  HTTP 403
  Server: cloudflare
  CF-RAY: a0c053ada97c2968-LHR
  Body title: "Attention Required! | Cloudflare"
  Body headline: "Sorry, you have been blocked"
  Marker matched: "Attention Required! | Cloudflare"
```

**Outcome:** Hard block confirmed. Adapter disabled. Decision drop written. RSS probe recommended as next step.

---

## Decision Drop Template

```markdown
# Decision Drop — <Source Name>: Cloudflare Anti-Bot Protection Confirmed

**Date:** YYYY-MM-DD  
**Author:** Michael (Backend / Scraping)  
**Status:** Proposed — awaiting Tommy review  

## Decision
Disable `<source>` adapter. Set `enabled = false` in `config.toml`.

## Evidence
| Signal | Value |
|---|---|
| HTTP status | 403 |
| Server header | cloudflare |
| CF-RAY | <value> |
| Body marker | <matched marker> |

Raw response: `tests/fixtures/adapters/<source>_recon.html`

## Why
[Explain why bypass is not attempted — charter prohibition]

## Impact
[-N sources. Overlap with other sources. Report quality impact.]

## Alternatives for Tommy
[Use alternatives table above]
```
