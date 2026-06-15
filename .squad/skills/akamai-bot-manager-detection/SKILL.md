# Akamai Bot Manager Detection

**Confidence:** medium (confirmed against totaljobs.com and cwjobs.co.uk, 2026-06-15)

## When to use

Before calibrating any new HTML-scrape adapter against a UK job board, run this detection probe to determine whether Akamai Bot Manager is in front of the site. If it is, **do not attempt to scrape** — disable the source and document, per the boundary in Michael's charter.

## Signals (any one is sufficient)

Run a normal `httpx.get` with a realistic browser User-Agent against the target URL and inspect both headers and body:

| Signal | Where | Meaning |
|---|---|---|
| Response header `x-akamai-transformed: 9 - 0 pmb=...` | response headers | Page rewritten by Akamai edge |
| Response header `Set-Cookie: _abck=...~-1~...` | response headers | Akamai session cookie; `~-1~` = not yet validated as human |
| Response header `Set-Cookie: ak-bmsc=...` | response headers | Bot Manager session cookie (legacy name) |
| Response header `Set-Cookie: bm_sv=...` | response headers | Bot Manager validation cookie |
| HTTP 200 + body 50-150KB + zero matches for the expected listing selector | response body | Akamai serving a stripped "splash" page |
| Body contains base64-encoded SVG blobs > 10KB at a stable offset | response body | Splash/loading page artifact rather than real content |

Header markers (`x-akamai-transformed` + `_abck`) are the most reliable. The body-level signals are useful confirmation when headers are inconclusive.

## What to do if detected

1. **Stop immediately.** Do NOT attempt TLS-fingerprint spoofing (curl-impersonate), headless-browser stealth plugins, or `_abck` cookie warming. These all qualify as ToS bypass.
2. Set `enabled = false` for the source in `config.toml` with an inline comment citing the probe date and findings.
3. Write a decision drop at `.squad/decisions/inbox/michael-{source}-akamai-blocked.md` listing at minimum:
   - Probe date and findings (which signals fired)
   - Alternatives to evaluate (RSS, official API, aggregator like Adzuna/JobServe)
   - Impact (which sources remain live)
4. **Keep any fixture HTML you already captured** — the file you saved via `Invoke-WebRequest` (which uses a different TLS stack than `httpx`) may still contain real listings. Fixture-based unit tests against that capture remain valid as future-proof reference.

## Reusable probe (httpx)

```python
import asyncio, httpx
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}

async def detect_akamai(url: str) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=30.0,
                                  follow_redirects=True) as c:
        r = await c.get(url)
    cookies_lower = str(r.headers).lower()
    return {
        "status": r.status_code,
        "body_len": len(r.text),
        "akamai_transformed": "x-akamai-transformed" in cookies_lower,
        "abck_unvalidated": "_abck=" in cookies_lower and "~-1~" in cookies_lower,
        "ak_bmsc": "ak-bmsc=" in cookies_lower,
        "bm_sv": "bm_sv=" in cookies_lower,
    }

asyncio.run(detect_akamai("https://example.com/jobs"))
```

A "true" on any of `akamai_transformed`, `abck_unvalidated`, `ak_bmsc`, or `bm_sv` means Akamai Bot Manager is present and the site should be marked blocked.

## Known UK job-board sources behind Akamai

- `www.totaljobs.com` (StepStone) — confirmed blocked 2026-06-15
- `www.cwjobs.co.uk` (StepStone) — confirmed blocked 2026-06-15

## Related skills

- `.squad/skills/cloudflare-detection/SKILL.md` — same playbook for Cloudflare
- `.squad/skills/dom-recon/SKILL.md` — what to do once you have a clean fetch
