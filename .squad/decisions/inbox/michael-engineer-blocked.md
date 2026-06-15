# Decision Drop — The Engineer Jobs: Cloudflare Anti-Bot Protection Confirmed

**Date:** 2026-06-15  
**Author:** Michael (Backend / Scraping)  
**Status:** Proposed — awaiting Tommy review  
**Affects:** `[sources.the_engineer]` in `config.toml`

---

## Decision

Disable the `the_engineer` adapter immediately. Set `enabled = false` in `config.toml`.  
Do not attempt to bypass Cloudflare. No further calibration work until an alternative fetch strategy is approved.

---

## Evidence

Live probe performed 2026-06-15T09:45Z against:

```
GET https://jobs.theengineer.co.uk/jobs/project-manager/?contract=contract
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...Chrome/131.0.0.0 Safari/537.36
```

| Signal | Value |
|---|---|
| HTTP status | **403 Forbidden** |
| `Server` header | `cloudflare` |
| `CF-RAY` header | `a0c053ada97c2968-LHR` |
| Body title | `Attention Required! | Cloudflare` |
| Body headline | *"Sorry, you have been blocked"* |
| CF challenge markers | `Attention Required! | Cloudflare` |

This is a **hard Cloudflare block** — not a JS challenge (which would return 200 with a CAPTCHA page). The edge node is returning an outright 403 before the request reaches the origin server. A realistic Chrome User-Agent on its own is insufficient to pass this gate.

Raw response saved to: `tests/fixtures/adapters/the_engineer_recon.html`

---

## Why

Cloudflare anti-bot protection blocks all server-side HTTP clients at the edge level for this host. The adapter's two-phase strategy (httpx → Playwright) cannot proceed because:

- **Phase A (httpx):** 403 at edge — never reaches origin. Browser headers alone do not satisfy Cloudflare's bot score.
- **Phase B (Playwright headless):** Headless Chromium is detectable by Cloudflare's BotFight/Turnstile fingerprinting (JS challenge features, canvas entropy, WebGL, missing browser APIs). Not guaranteed to pass; would require stealth-patching which violates ToS.

Attempting to bypass Cloudflare would violate Michael's charter: *"Must NOT bypass site ToS or scrape behind authentication walls that prohibit it."*

---

## Impact

- **-1 source** temporarily removed from the 7-source MVP.
- Remaining 6 sources (Reed, Totaljobs, CWJobs, RailwayPeople, Energy Jobline, Aviation Job Search) continue unaffected.
- No immediate report quality loss — The Engineer Jobs overlaps significantly with Totaljobs/CWJobs (both index engineering PM contracts) and partially with Reed.
- Weekly scheduled runs are unaffected.

---

## Alternatives for Tommy to Evaluate

### (a) Playwright + stealth patching — RISKY
Playwright headless with [`playwright-stealth`](https://github.com/AtuboDad/playwright-stealth) or a patched Chromium (e.g. `undetected-playwright`) can sometimes pass Cloudflare BotFight.
- **Pros:** Reuses existing `mechpm[browser]` extra; no new infra.
- **Cons:** Arms-race maintenance; Cloudflare updates fingerprint detection regularly; may still fail on Turnstile challenges; ethically questionable if it circumvents an explicit block.
- **Recommendation:** Low — if Cloudflare is actively blocking, stealth patching is a fragile hack.

### (b) RSS feed — PREFERRED if available
Check whether The Engineer publishes an RSS feed for their jobs board.
- Probe URLs: `https://jobs.theengineer.co.uk/rss`, `/feed`, `/rss.xml`, `/jobs/rss`
- If a feed exists, it bypasses Cloudflare entirely (RSS is typically served without bot protection) and is explicitly ToS-friendly.
- **Action:** Tommy or Michael probes these URLs (simple `Invoke-WebRequest` — no JS needed).

### (c) Email job alerts
The Engineer Jobs may offer email job alerts for saved searches.
- Register a dedicated inbox, set up a "project manager contract" alert.
- Add an IMAP/email-polling adapter (Michael can build; no Cloudflare interaction).
- **Pros:** Zero anti-bot risk; ToS-friendly; reliable.
- **Cons:** Latency (daily digest at best); requires managed inbox.

### (d) Partnership / data feed
The Engineer Jobs is published by Mark Allen Group. They may offer a commercial data feed or job XML feed for aggregators.
- **Action:** Steve or Tommy contact their sales/partnerships team.
- **Pros:** Fully authorized; highest data fidelity.
- **Cons:** Likely paid; procurement overhead.

### (e) Drop the source for MVP
Accept -1 source and revisit in v0.2 once a clean strategy is identified.
- **Pros:** Zero risk; no engineering effort.
- **Cons:** Loses the UK's leading engineering trade publication as a source.

---

## Recommended Next Step

Tommy to probe RSS endpoints (option b) — single `Invoke-WebRequest` call, zero risk, takes 2 minutes. If a feed exists, Michael can build an RSS adapter in ~1 sprint. If not, Tommy to decide between options (c), (d), and (e).
