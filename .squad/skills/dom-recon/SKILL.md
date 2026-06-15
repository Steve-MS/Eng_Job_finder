# SKILL: Live DOM Recon Protocol for New Job Board Adapters

**Author:** Michael (Backend / Scraping)  
**Created:** 2026-06-15  
**Validated against:** Aviation Job Search (2026-06-15), Energy Jobline (2026-06-15), RailwayPeople (2026-06-14)

---

## Purpose

Before writing or calibrating a job board adapter, run this recon protocol to determine:
1. Whether the page is SSR (server-rendered) or CSR (client-side rendered / AJAX)
2. What the listing data format is (HTML DOM, `__NEXT_DATA__` JSON, LD+JSON, or external API)
3. Whether any anti-bot protection will block the adapter
4. What fields are available and where

Completing this protocol before writing code prevents the most common adapter failure mode: **building a CSS-selector scraper for a page that renders nothing in static HTML**.

---

## Step 1: Check robots.txt

```powershell
Invoke-WebRequest -Uri "https://{domain}/robots.txt" -UseBasicParsing | Select-Object -Expand Content
```

**Record:**
- Any `Disallow:` for `/api/*`, `/search/*`, or `/jobs/*`
- Any `Crawl-delay:`
- Sitemap URL(s) in `Sitemap:` directive

---

## Step 2: Fetch the search results page and check basic structure

```powershell
$headers = @{
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
    "Accept" = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    "Accept-Language" = "en-GB,en;q=0.9"
    # NOTE: Do NOT include "Accept-Encoding" unless you know httpx can handle the encoding
}
$r = Invoke-WebRequest -Uri "{search_url}" -Headers $headers -UseBasicParsing -TimeoutSec 30
Write-Host "Status: $($r.StatusCode), Length: $($r.Content.Length)"
```

**Check for:**
- HTTP status (200 vs 403/503)
- `Content-Length` — very short responses often indicate bot blocks or redirects
- `<title>` tag — does it match the expected search results page?

---

## Step 3: Detect rendering strategy

Run these checks **in order** — the first positive hit determines your adapter strategy:

### Check A: `__NEXT_DATA__` (Next.js SSR)
```powershell
if ($r.Content -match '__NEXT_DATA__') { Write-Host "NEXT.JS: Parse __NEXT_DATA__ JSON blob" }
```
→ If found: use `_find_jobs_list()` recursive search pattern (see `railwaypeople.py`)

### Check B: `AppData.is_ssr` (client-side app indicator)
```powershell
[regex]::Match($r.Content, 'is_ssr[^,]{0,50}')
```
→ If `is_ssr: false`: page is **fully AJAX-rendered** — no listings in static HTML. Do not use CSS selectors.

### Check C: Embedded JSON state
```powershell
[regex]::Matches($r.Content, '(?s)(const|var|let)\s+(AppData|initialState|jobData|pageData|searchData)\s*=\s*(\{[^;]+\})')
```
→ If found: parse the embedded state object — may contain listing data

### Check D: LD+JSON on detail pages
If the search results page has no listings, check if **individual job detail pages** are SSR with schema.org data:
```powershell
# Get a sample job URL from the sitemap first
$sitemap = Invoke-WebRequest -Uri "https://{domain}/sitemap.xml" -UseBasicParsing
$jobUrl = ([regex]::Matches($sitemap.Content, '<loc>([^<]+/jobs/[^<]+)</loc>') | Select-Object -First 1).Groups[1].Value
$jobPage = Invoke-WebRequest -Uri $jobUrl -UseBasicParsing
$ldJson = [regex]::Matches($jobPage.Content, '(?s)<script type="application/ld\+json">(.+?)</script>') | Where-Object { $_.Groups[1].Value -match 'JobPosting' }
if ($ldJson) { Write-Host "LD+JSON JobPosting found — use sitemap + LD+JSON strategy" }
```
→ If found: use **sitemap + LD+JSON strategy** (see `aviation_job_search.py`)

### Check E: Plain HTML listing repeater
```powershell
$patterns = @('<article', 'class="job', 'data-job-id', 'job-listing', 'job-result', 'job-card', '.listing-item')
foreach ($p in $patterns) {
    $c = ([regex]::Matches($r.Content, [regex]::Escape($p))).Count
    if ($c -gt 3) { Write-Host "HTML pattern '$p': $c occurrences — use CSS selector strategy" }
}
```
→ If multiple occurrences: standard CSS-selector scraping applies

---

## Step 4: Find the API endpoint (if AJAX-rendered)

If Step 3B confirmed AJAX rendering, find the endpoint:
```powershell
# Extract inline scripts
$inlineScripts = [regex]::Matches($r.Content, '(?s)<script(?![^>]*src)[^>]*>(.*?)</script>')
foreach ($m in $inlineScripts) {
    $content = $m.Groups[1].Value
    if ($content -match 'api.*jobs|jobs.*api|fetch.*jobs|jobs.*search') {
        Write-Host $content.Substring(0, [Math]::Min(500, $content.Length))
    }
}
```

**Critical:** If the endpoint is under a `Disallow`-ed path (e.g. `/api/*`), **do not use it**. Use the sitemap or Playwright instead.

---

## Step 5: Check for Brotli encoding trap

When using browser-like headers, check `Content-Encoding` on key responses:
```powershell
$resp = Invoke-WebRequest -Uri "{url}" -Headers $headers -UseBasicParsing
$resp.Headers['Content-Encoding']  # If 'br' — httpx will not decompress it!
```

**Rule:** If server returns `Content-Encoding: br`:
- Either install `brotli` PyPI package
- Or **remove `br` from `Accept-Encoding`** in the adapter headers: `"Accept-Encoding": "gzip, deflate"`

httpx silently fails to decode Brotli without the optional package — the response text will be garbled binary data.

---

## Step 6: Inspect LD+JSON schema (if applicable)

```python
import json
from selectolax.parser import HTMLParser

tree = HTMLParser(html)
for node in tree.css("script"):
    if "ld+json" in (node.attributes.get("type") or "").lower():
        data = json.loads(node.text())
        print(json.dumps(data, indent=2)[:2000])
```

**Common schema.org/JobPosting fields:**
- `title` → RawListing.title
- `hiringOrganization.name` → RawListing.employer
- `jobLocation.address.{addressLocality, addressRegion, addressCountry}` → RawListing.location_raw
- `datePosted` → RawListing.posted_at
- `employmentType` → RawListing.contract_type_raw
- `description` → RawListing.description_raw
- `baseSalary` → RawListing.salary_raw (rarely populated on UK job boards)
- Listing ID: extract trailing `-NNN` numeric segment from URL slug

---

## Strategy Decision Tree

```
robots.txt OK?
  └─ NO  → Disable adapter, write decision drop
  └─ YES →
    Fetch search page → __NEXT_DATA__ present?
      └─ YES → Use __NEXT_DATA__ JSON strategy (see railwaypeople.py)
      └─ NO  → is_ssr = false / #results div empty?
        └─ YES (AJAX) →
          API endpoint disallowed in robots.txt?
            └─ YES → sitemap available with SSR job-detail pages?
              └─ YES + LD+JSON present → Use sitemap + LD+JSON strategy (see aviation_job_search.py)
              └─ NO  → Playwright if approved, else disable
            └─ NO  → Call API directly (JSON response)
        └─ NO (SSR HTML) →
          Cloudflare / WAF blocking?
            └─ YES → Document + disable (see cloudflare-detection/SKILL.md)
            └─ NO  → Use CSS selector strategy
```

---

## Reusable Code Patterns

### Sitemap filter
```python
_SITEMAP_RE = re.compile(
    r"<url>\s*<loc>([^<]+)</loc>"
    r"(?:.*?<lastmod>([^<]+)</lastmod>)?",
    re.DOTALL,
)
urls = [m.group(1).strip() for m in _SITEMAP_RE.finditer(sitemap_xml)]
```

### LD+JSON extraction
```python
def _parse_ldjson_job(html: str) -> dict | None:
    tree = HTMLParser(html)
    for node in tree.css("script"):
        if "ld+json" not in (node.attributes.get("type") or "").lower():
            continue
        try:
            data = json.loads(node.text())
        except (json.JSONDecodeError, Exception):
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None
```

### Job ID from URL slug
```python
_JOB_ID_RE = re.compile(r"-(\d+)$")
def _extract_job_id(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    m = _JOB_ID_RE.search(slug)
    return m.group(1) if m else slug
```
