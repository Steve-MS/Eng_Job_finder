"""HTML report renderer — converts NormalizedListing objects to a self-contained HTML report.

Mirrors the section order of render.py (Markdown) but produces a single-file
HTML document with embedded CSS suitable for opening locally in a browser.
All business logic (grouping, banding, IR35 badges, rate formatting) is
imported from the existing render.py / domain.py / grouping.py modules — only
the formatting layer changes.

Section order (matches Markdown):
    1. Header + Summary block
    2. 🆕 New This Week
    3. 💰 Premium Rate
    4. ⚡ Urgent Starts
    5. All Current Roles — By Region
    6. ⚠️ Review Queue
    7. Footer (generation timestamp, total count)

2026-06-15
"""
from __future__ import annotations

import html as html_mod
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from mechpm.models import NormalizedListing
from mechpm.reporter.domain import effective_day_rate, rate_context
from mechpm.reporter.grouping import (
    REGION_ORDER,
    get_sanity_reasons,
    get_soft_notes,
    group_by_region,
    is_geo_flagged,
    is_premium,
    is_sanity_flagged,
    is_urgent,
    resolve_region,
)
from mechpm.reporter.models import RunMetadata
from mechpm.reporter.render import (
    _classify_seniority,
    _duration_str,
    _next_friday_from,
    _rate_str,
    _source_name_from_url,
    _start_str_safe,
    _SENIORITY_LABELS,
    _REGION_FLAGS,
    _SUMMARY_MAX_CHARS,
    _SUMMARY_COMPACT_CHARS,
)

# ---------------------------------------------------------------------------
# Embedded CSS — single source of truth for the report style
# ---------------------------------------------------------------------------

_CSS = """
:root {
    --bg:           #f7f8fa;
    --card-bg:      #ffffff;
    --border:       #e2e5ea;
    --accent:       #1a56db;
    --accent-hover: #1040b0;
    --text:         #1a1d23;
    --muted:        #6b7280;
    --ir35-outside:  #166534;
    --ir35-out-bg:   #dcfce7;
    --ir35-inside:   #92400e;
    --ir35-in-bg:    #fef3c7;
    --ir35-umbrella: #1e40af;
    --ir35-umb-bg:   #dbeafe;
    --ir35-none:     #374151;
    --ir35-none-bg:  #f3f4f6;
    --tag-bg:        #f1f5f9;
    --tag-text:      #475569;
    --new-bg:        #eff6ff;
    --premium-bg:    #fdf4ff;
    --urgent-bg:     #fff7ed;
    --review-bg:     #fffbeb;
    --section-h:     #0f172a;
    --divider:       #e2e5ea;
    --flag-new:      #2563eb;
    --flag-premium:  #7e22ce;
    --flag-urgent:   #ea580c;
    --flag-warn:     #b45309;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    line-height: 1.6;
    padding: 24px 16px 48px;
}

/* Layout wrapper */
.report-wrap {
    max-width: 960px;
    margin: 0 auto;
}

/* ---- Report header ---- */
.report-header {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 24px 28px;
    margin-bottom: 24px;
}
.report-header h1 {
    font-size: 22px;
    font-weight: 700;
    color: var(--section-h);
    margin-bottom: 8px;
}
.report-meta {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 4px 24px;
    margin-top: 12px;
    font-size: 13px;
    color: var(--muted);
}
.report-meta span strong { color: var(--text); }
.report-meta .sources-failed { color: #b91c1c; }

/* ---- Summary block ---- */
.summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 28px;
}
.summary-tile {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;
    user-select: none;
}
.summary-tile:hover { border-color: var(--accent); box-shadow: 0 2px 8px rgba(0,0,0,0.08); transform: translateY(-1px); }
.summary-tile:active { transform: translateY(0); }
.summary-tile.tile-active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent); background: #f0f7ff; }
.summary-tile .count { font-size: 28px; font-weight: 700; color: var(--accent); }
.summary-tile .label { font-size: 12px; color: var(--muted); margin-top: 2px; }

/* ---- Section headers ---- */
section { margin-bottom: 32px; }
section > header {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--divider);
}
section > header h2 {
    font-size: 18px;
    font-weight: 700;
    color: var(--section-h);
}
section > header .section-count {
    font-size: 13px;
    color: var(--muted);
    font-weight: 400;
}
.section-note {
    font-size: 13px;
    color: var(--muted);
    margin-bottom: 12px;
    font-style: italic;
}
.empty-section {
    color: var(--muted);
    font-style: italic;
    font-size: 13px;
    padding: 12px 0;
}

/* ---- Role cards ---- */
article.role-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px 20px;
    margin-bottom: 12px;
    position: relative;
}
article.role-card.card-new    { border-left: 3px solid var(--flag-new); background: var(--new-bg); }
article.role-card.card-premium { border-left: 3px solid var(--flag-premium); background: var(--premium-bg); }
article.role-card.card-urgent  { border-left: 3px solid var(--flag-urgent); background: var(--urgent-bg); }
article.role-card.card-review  { border-left: 3px solid var(--flag-warn); background: var(--review-bg); }
article.role-card.card-pipeline { border-left: 3px solid var(--border); }

/* Card title row */
.card-title-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 6px;
    flex-wrap: wrap;
}
.card-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--accent);
    text-decoration: none;
    line-height: 1.4;
}
.card-title:hover { color: var(--accent-hover); text-decoration: underline; }
.card-employer {
    font-size: 13px;
    color: var(--muted);
    margin-bottom: 8px;
}
.card-location {
    font-size: 13px;
    color: var(--muted);
    margin-bottom: 8px;
}
/* Flag pills (🆕⚡💰⚠️) */
.flag-row {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}
.flag-pill {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 9999px;
    letter-spacing: 0.02em;
}
.flag-new     { background: #dbeafe; color: var(--flag-new); }
.flag-premium { background: #f3e8ff; color: var(--flag-premium); }
.flag-urgent  { background: #ffedd5; color: var(--flag-urgent); }
.flag-warn    { background: #fef3c7; color: var(--flag-warn); }

/* Detail row */
.card-details {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    margin-bottom: 8px;
    font-size: 13px;
}
.detail-item { display: flex; align-items: center; gap: 5px; }
.detail-label { color: var(--muted); font-weight: 500; }

/* IR35 badge */
.ir35-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 9999px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}
.ir35-outside  { background: var(--ir35-out-bg); color: var(--ir35-outside); }
.ir35-inside   { background: var(--ir35-in-bg);  color: var(--ir35-inside); }
.ir35-umbrella { background: var(--ir35-umb-bg); color: var(--ir35-umbrella); }
.ir35-none     { background: var(--ir35-none-bg); color: var(--ir35-none); }

/* Rate band tag */
.rate-band-tag {
    display: inline-block;
    font-size: 11px;
    color: var(--tag-text);
    background: var(--tag-bg);
    padding: 1px 7px;
    border-radius: 9999px;
    border: 1px solid var(--border);
}

/* Summary snippet */
.card-summary {
    font-size: 13px;
    color: var(--muted);
    margin: 8px 0;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

/* Source links row */
.card-source-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 10px;
    font-size: 12px;
    color: var(--muted);
}
a.view-listing-btn {
    display: inline-block;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 12px;
    border-radius: 6px;
    border: 1px solid var(--accent);
    color: var(--accent);
    text-decoration: none;
    transition: background 0.15s, color 0.15s;
}
a.view-listing-btn:hover {
    background: var(--accent);
    color: #fff;
}
a.source-link {
    color: var(--accent);
    text-decoration: none;
    font-size: 12px;
}
a.source-link:hover { text-decoration: underline; }

/* Soft notes */
.soft-note {
    font-size: 12px;
    color: var(--flag-warn);
    margin-top: 6px;
    padding: 4px 8px;
    background: #fef9c3;
    border-radius: 4px;
    border-left: 3px solid #fbbf24;
}

/* Sanity reasons */
.sanity-reasons {
    font-size: 12px;
    color: var(--flag-warn);
    margin-top: 6px;
    padding: 6px 10px;
    background: var(--review-bg);
    border-radius: 4px;
    border-left: 3px solid #fbbf24;
}
.sanity-reasons ul { margin-left: 16px; }

/* Region sub-section */
.region-section { margin-bottom: 24px; }
.region-heading {
    font-size: 16px;
    font-weight: 600;
    color: var(--section-h);
    margin-bottom: 10px;
    padding-bottom: 5px;
    border-bottom: 1px solid var(--divider);
}
.seniority-heading {
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin: 12px 0 8px;
}

/* Data quality box */
.data-quality-box {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px 20px;
    font-size: 13px;
    margin-bottom: 24px;
}
.data-quality-box h3 {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 10px;
    color: var(--section-h);
}
.data-quality-box ul { margin-left: 18px; color: var(--muted); }
.data-quality-box li { margin-bottom: 4px; }
.src-ok   { color: #166534; }
.src-fail { color: #b91c1c; }

/* Footer */
footer {
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--divider);
    font-size: 12px;
    color: var(--muted);
    text-align: center;
    line-height: 1.8;
}
footer strong { color: var(--text); }

/* ---- Combined filter bar (dropdown selects) ---- */
.filter-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 12px 20px;
    align-items: center;
    margin-bottom: 24px;
    padding: 14px 18px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
}
.filter-group {
    display: flex;
    align-items: center;
    gap: 8px;
}
.filter-bar-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    white-space: nowrap;
}
.filter-select {
    font-size: 13px;
    font-family: inherit;
    color: var(--text);
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 5px 28px 5px 10px;
    cursor: pointer;
    -webkit-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%236b7280' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 8px center;
    min-width: 160px;
    transition: border-color 0.15s;
}
.filter-select:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(26,86,219,0.15);
}
.filter-count-label { font-size: 12px; color: var(--muted); margin-left: auto; font-style: italic; }
"""

# ---------------------------------------------------------------------------
# HTML escape helper
# ---------------------------------------------------------------------------

def _h(text: str | None) -> str:
    """HTML-escape a plain-text value; empty string when None."""
    return html_mod.escape(str(text)) if text is not None else ""


def _truncate_plain(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    # Truncate at word boundary.
    t = text[:max_chars]
    idx = t.rfind(" ")
    return (t[:idx] if idx > max_chars // 2 else t) + "…"


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------

_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "reed":                 "Reed",
    "adzuna":               "Adzuna",
    "energy-jobline":       "Energy Jobline",
    "railwaypeople":        "RailwayPeople",
    "aviation-job-search":  "Aviation Job Search",
    "the-engineer":         "The Engineer",
    "totaljobs":            "Totaljobs",
    "cwjobs":               "CWJobs",
    "linkedin":             "LinkedIn",
}


def _source_id(listing: NormalizedListing) -> str:
    """Return a kebab-case source identifier suitable for data-source attribute."""
    return (listing.source or "unknown").replace("_", "-").lower()


def _source_display_name(sid: str) -> str:
    return _SOURCE_DISPLAY_NAMES.get(sid, sid.replace("-", " ").title())


# ---------------------------------------------------------------------------
# Region helpers
# ---------------------------------------------------------------------------

_REGION_ID_TO_DISPLAY: dict[str, str] = {
    r.lower().replace(" ", "-"): r for r in REGION_ORDER
}


def _region_id(listing: NormalizedListing) -> str:
    """Return a kebab-case region identifier for the data-region attribute."""
    region = resolve_region(listing.location_normalized or "")
    return region.lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# Job type classification
# ---------------------------------------------------------------------------

import re as _re

_JOBTYPE_RULES: list[tuple[_re.Pattern[str], str]] = [
    (_re.compile(r"document\s+control|records\s+control", _re.IGNORECASE), "Document Controller"),
    (_re.compile(r"assurance|\bsqa\b", _re.IGNORECASE), "Assurance"),
    (_re.compile(r"planner|planning\s+engineer", _re.IGNORECASE), "Planner"),
    (_re.compile(r"site\s+manager", _re.IGNORECASE), "Site Manager"),
    (_re.compile(r"project\s+engineer", _re.IGNORECASE), "Project Engineer"),
    (_re.compile(r"project\s+manager|programme\s+manager|commissioning", _re.IGNORECASE), "Project Manager"),
]

_JOBTYPE_SLUG: dict[str, str] = {
    "Project Manager":      "project-manager",
    "Project Engineer":     "project-engineer",
    "Assurance":            "assurance",
    "Document Controller":  "document-controller",
    "Site Manager":         "site-manager",
    "Planner":              "planner",
    "Other":                "other",
}

_JOBTYPE_ORDER: list[str] = [
    "project-manager",
    "project-engineer",
    "assurance",
    "document-controller",
    "site-manager",
    "planner",
    "other",
]

_JOBTYPE_DISPLAY: dict[str, str] = {v: k for k, v in _JOBTYPE_SLUG.items()}


def classify_job_type(title: str) -> str:
    """Classify a job title into one of the canonical role families.

    Returns one of: 'Project Manager', 'Project Engineer', 'Assurance',
    'Document Controller', 'Site Manager', 'Planner', 'Other'.
    First-match wins; checks are case-insensitive.
    """
    for pattern, category in _JOBTYPE_RULES:
        if pattern.search(title):
            return category
    return "Other"


def _jobtype_id(listing: NormalizedListing) -> str:
    """Return a kebab-case job-type identifier for the data-jobtype attribute."""
    category = classify_job_type(listing.title or "")
    return _JOBTYPE_SLUG.get(category, "other")


# ---------------------------------------------------------------------------
# Filter bar
# ---------------------------------------------------------------------------

def _render_combined_filter_bar(listings: list[NormalizedListing]) -> str:
    """Render a single filter row containing three <select> dropdowns.

    Dropdowns cover source, region, and job type.  Options are populated
    dynamically from the actual listings in this report, so any new categories
    added to the pipeline (e.g. new job types from Ada) appear automatically.

    The bar is hidden until JS runs (progressive enhancement): with JS
    disabled all cards remain visible and the bar stays hidden.
    """
    from collections import Counter

    if not listings:
        return ""

    total = len(listings)

    # Source options — sorted by frequency descending
    src_counts: Counter[str] = Counter(_source_id(l) for l in listings)
    src_sorted = sorted(src_counts.items(), key=lambda x: -x[1])
    src_options = "".join(
        f'      <option value="{_h(sid)}">{_h(_source_display_name(sid))} ({cnt})</option>\n'
        for sid, cnt in src_sorted
    )

    # Region options — canonical REGION_ORDER, then any unlisted extras
    rgn_counts: Counter[str] = Counter(_region_id(l) for l in listings)
    region_order_ids = [r.lower().replace(" ", "-") for r in REGION_ORDER]
    rgn_sorted = [(rid, rgn_counts[rid]) for rid in region_order_ids if rid in rgn_counts]
    for rid, cnt in sorted(rgn_counts.items()):
        if rid not in {r for r, _ in rgn_sorted}:
            rgn_sorted.append((rid, cnt))
    rgn_options = "".join(
        f'      <option value="{_h(rid)}">{_h(_REGION_ID_TO_DISPLAY.get(rid, rid.replace("-", " ").title()))} ({cnt})</option>\n'
        for rid, cnt in rgn_sorted
    )

    # Job-type options — _JOBTYPE_ORDER first, then any dynamically discovered extras
    jt_counts: Counter[str] = Counter(_jobtype_id(l) for l in listings)
    known_ids = list(_JOBTYPE_ORDER)
    jt_sorted = [(jid, jt_counts[jid]) for jid in known_ids if jid in jt_counts]
    for jid, cnt in sorted(jt_counts.items()):
        if jid not in {j for j, _ in jt_sorted}:
            jt_sorted.append((jid, cnt))
    jt_options = "".join(
        f'      <option value="{_h(jid)}">{_h(_JOBTYPE_DISPLAY.get(jid, jid.replace("-", " ").title()))} ({cnt})</option>\n'
        for jid, cnt in jt_sorted
    )

    # IR35 status options — ordered: outside, inside, umbrella, not-stated
    _IR35_ORDER = ["outside", "inside", "umbrella", "not-stated"]
    _IR35_DISPLAY = {
        "outside": "Outside IR35",
        "inside": "Inside IR35",
        "umbrella": "Umbrella",
        "not-stated": "Not Stated",
    }
    ir35_counts: Counter[str] = Counter((l.ir35_status or "not-stated") for l in listings)
    ir35_sorted = [(iid, ir35_counts[iid]) for iid in _IR35_ORDER if iid in ir35_counts]
    for iid, cnt in sorted(ir35_counts.items()):
        if iid not in {i for i, _ in ir35_sorted}:
            ir35_sorted.append((iid, cnt))
    ir35_options = "".join(
        f'      <option value="{_h(iid)}">{_h(_IR35_DISPLAY.get(iid, iid.replace("-", " ").title()))} ({cnt})</option>\n'
        for iid, cnt in ir35_sorted
    )

    counter = (
        f'<span id="filter-count" class="filter-count-label">Showing all {total} listings</span>'
    )

    return (
        f'<div id="filter-bar" class="filter-bar" style="display:none">\n'
        f'  <div class="filter-group">\n'
        f'    <label class="filter-bar-label" for="filter-source">Source:</label>\n'
        f'    <select id="filter-source" class="filter-select">\n'
        f'      <option value="">All Sources</option>\n'
        f"{src_options}"
        f'    </select>\n'
        f'  </div>\n'
        f'  <div class="filter-group">\n'
        f'    <label class="filter-bar-label" for="filter-region">Region:</label>\n'
        f'    <select id="filter-region" class="filter-select">\n'
        f'      <option value="">All Regions</option>\n'
        f"{rgn_options}"
        f'    </select>\n'
        f'  </div>\n'
        f'  <div class="filter-group">\n'
        f'    <label class="filter-bar-label" for="filter-jobtype">Job Type:</label>\n'
        f'    <select id="filter-jobtype" class="filter-select">\n'
        f'      <option value="">All Job Types</option>\n'
        f"{jt_options}"
        f'    </select>\n'
        f'  </div>\n'
        f'  <div class="filter-group">\n'
        f'    <label class="filter-bar-label" for="filter-ir35">IR35:</label>\n'
        f'    <select id="filter-ir35" class="filter-select">\n'
        f'      <option value="">All IR35</option>\n'
        f"{ir35_options}"
        f'    </select>\n'
        f'  </div>\n'
        f'  {counter}\n'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Embedded filter JavaScript
# ---------------------------------------------------------------------------

_FILTER_JS = """<script>
(function(){
  var bar=document.getElementById('filter-bar');
  if(bar)bar.style.display='flex';
  var selSrc=document.getElementById('filter-source');
  var selRgn=document.getElementById('filter-region');
  var selJt=document.getElementById('filter-jobtype');
  var selIr35=document.getElementById('filter-ir35');
  var allCards=document.querySelectorAll('article.role-card[data-source]');
  var total=allCards.length;
  var tiles=document.querySelectorAll('.summary-tile[data-filter]');
  var activeTileFilter='';

  function applyFilter(){
    var src=selSrc?selSrc.value:'';
    var rgn=selRgn?selRgn.value:'';
    var jt=selJt?selJt.value:'';
    var ir=selIr35?selIr35.value:'';
    var tf=activeTileFilter;
    var vis=0;
    allCards.forEach(function(c){
      var show=(!src||c.dataset.source===src)
        &&(!rgn||c.dataset.region===rgn)
        &&(!jt||c.dataset.jobtype===jt)
        &&(!ir||c.dataset.ir35===ir);
      if(show&&tf&&tf!=='all'){
        show=c.dataset[tf]==='1';
      }
      c.style.display=show?'':'none';
      if(show)vis++;
    });
    // Also show/hide empty section content
    document.querySelectorAll('section').forEach(function(sec){
      var cards=sec.querySelectorAll('article.role-card');
      if(cards.length===0)return;
      var anyVis=false;
      cards.forEach(function(c){if(c.style.display!=='none')anyVis=true;});
      var empties=sec.querySelectorAll('.empty-filter-msg');
      empties.forEach(function(e){e.remove();});
      if(!anyVis&&(tf||src||rgn||jt||ir)){
        var msg=document.createElement('p');
        msg.className='empty-section empty-filter-msg';
        msg.textContent='No matching listings in this section.';
        sec.appendChild(msg);
      }
    });
    var lbl=document.getElementById('filter-count');
    if(lbl){
      if(!tf&&!src&&!rgn&&!jt&&!ir) lbl.textContent='Showing all '+total+' listings';
      else lbl.textContent='Showing '+vis+' of '+total+' listings';
    }
  }

  tiles.forEach(function(tile){
    tile.addEventListener('click',function(){
      var f=tile.dataset.filter;
      if(f===activeTileFilter||f==='all'){
        activeTileFilter='';
        tiles.forEach(function(t){t.classList.remove('tile-active');});
      } else {
        activeTileFilter=f;
        tiles.forEach(function(t){t.classList.remove('tile-active');});
        tile.classList.add('tile-active');
      }
      applyFilter();
    });
    tile.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){e.preventDefault();tile.click();}
    });
  });

  if(selSrc)selSrc.addEventListener('change',applyFilter);
  if(selRgn)selRgn.addEventListener('change',applyFilter);
  if(selJt)selJt.addEventListener('change',applyFilter);
  if(selIr35)selIr35.addEventListener('change',applyFilter);
  applyFilter();
})();
</script>"""


# ---------------------------------------------------------------------------
# IR35 badge
# ---------------------------------------------------------------------------

def _ir35_badge_html(ir35: str | None) -> str:
    if ir35 == "outside":
        return '<span class="ir35-badge ir35-outside">Outside IR35</span>'
    if ir35 == "inside":
        return '<span class="ir35-badge ir35-inside">Inside IR35</span>'
    if ir35 == "umbrella":
        return '<span class="ir35-badge ir35-umbrella">Umbrella</span>'
    return '<span class="ir35-badge ir35-none">IR35 Not Stated</span>'


# ---------------------------------------------------------------------------
# Flag pills
# ---------------------------------------------------------------------------

def _flag_pills(listing: NormalizedListing, today: date) -> str:
    parts: list[str] = []
    if listing.is_new_listing:
        parts.append('<span class="flag-pill flag-new">🆕 New</span>')
    if is_urgent(listing, today):
        parts.append('<span class="flag-pill flag-urgent">⚡ Urgent Start</span>')
    if is_premium(listing):
        parts.append('<span class="flag-pill flag-premium">💰 Premium Rate</span>')
    if is_sanity_flagged(listing, today):
        parts.append('<span class="flag-pill flag-warn">⚠️ Review</span>')
    if not parts:
        return ""
    return f'<div class="flag-row">{"".join(parts)}</div>'


# ---------------------------------------------------------------------------
# Source links block
# ---------------------------------------------------------------------------

def _source_links_html(listing: NormalizedListing) -> str:
    """Return HTML for source attribution + clickable view-listing buttons."""
    urls = listing.source_urls or ([listing.source_url] if listing.source_url else [])
    if not urls:
        return f'<span class="source-label">Source: {_h(listing.source)}</span>'

    parts: list[str] = []
    for url in urls:
        name = _h(_source_name_from_url(url))
        escaped_url = _h(url)
        parts.append(
            f'<span class="source-name">{name}</span>'
            f'<a class="view-listing-btn" href="{escaped_url}" target="_blank" rel="noopener">View Full Listing</a>'
        )
    # If multiple URLs (cross-source dedup), also show individual named links.
    if len(urls) > 1:
        named: list[str] = []
        for url in urls:
            name = _h(_source_name_from_url(url))
            escaped_url = _h(url)
            named.append(f'<a class="source-link" href="{escaped_url}" target="_blank" rel="noopener">{name}</a>')
        parts.append(" · ".join(named))

    return '<div class="card-source-row">' + " ".join(parts) + "</div>"


# ---------------------------------------------------------------------------
# Card builder (shared across section types)
# ---------------------------------------------------------------------------

def _role_card(
    listing: NormalizedListing,
    today: date,
    card_class: str = "card-pipeline",
    show_summary_chars: int = _SUMMARY_COMPACT_CHARS,
    sanity_reasons: list[str] | None = None,
) -> str:
    lines: list[str] = []

    # Title linked to primary source URL.
    primary_url = (listing.source_urls or [listing.source_url])[0] if (listing.source_urls or listing.source_url) else ""
    title_escaped = _h(listing.title)
    if primary_url:
        title_html = f'<a class="card-title" href="{_h(primary_url)}" target="_blank" rel="noopener">{title_escaped}</a>'
    else:
        title_html = f'<span class="card-title">{title_escaped}</span>'

    employer = _h(listing.employer or listing.agency or "Unknown Employer")
    location = _h(listing.location or "Location TBC")

    rate_band = _h(rate_context(listing))
    rate_display = _h(_rate_str(listing))
    ir35_html = _ir35_badge_html(listing.ir35_status)
    duration = _h(_duration_str(listing.duration_weeks))
    start = _h(_start_str_safe(listing.start_date, today))
    flags = _flag_pills(listing, today)

    lines.append(f'<article class="role-card {card_class}"'
                 f' data-source="{_h(_source_id(listing))}"'
                 f' data-region="{_h(_region_id(listing))}"'
                 f' data-jobtype="{_h(_jobtype_id(listing))}"'
                 f' data-ir35="{_h(listing.ir35_status or "not-stated")}"'
                 f' data-new="{"1" if listing.is_new_listing else "0"}"'
                 f' data-urgent="{"1" if is_urgent(listing, today) else "0"}"'
                 f' data-premium="{"1" if is_premium(listing) else "0"}"'
                 f' data-flagged="{"1" if is_sanity_flagged(listing, today) else "0"}"'
                 f'>')

    if flags:
        lines.append(flags)

    lines.append(f'<div class="card-title-row">{title_html}</div>')
    lines.append(f'<div class="card-employer">{employer}</div>')
    lines.append(f'<div class="card-location">📍 {location}</div>')

    # Details row.
    lines.append('<div class="card-details">')
    lines.append(f'  <span class="detail-item"><span class="detail-label">Rate:</span> {rate_display} <span class="rate-band-tag">{rate_band}</span></span>')
    lines.append(f'  <span class="detail-item"><span class="detail-label">IR35:</span> {ir35_html}</span>')
    lines.append(f'  <span class="detail-item"><span class="detail-label">Duration:</span> {duration}</span>')
    lines.append(f'  <span class="detail-item"><span class="detail-label">Start:</span> {start}</span>')
    lines.append('</div>')

    # Summary snippet.
    if listing.description_clean:
        snippet = _h(_truncate_plain(listing.description_clean, show_summary_chars))
        lines.append(f'<p class="card-summary">{snippet}</p>')

    # Soft notes.
    soft = get_soft_notes(listing)
    if soft:
        lines.append(f'<div class="soft-note">💡 {_h("; ".join(soft))}</div>')

    # Sanity reasons (review queue).
    if sanity_reasons:
        reason_items = "".join(f"<li>{_h(r)}</li>" for r in sanity_reasons)
        lines.append(f'<div class="sanity-reasons"><strong>⚠️ Flagged:</strong><ul>{reason_items}</ul></div>')

    # Source links.
    lines.append(_source_links_html(listing))

    lines.append("</article>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_html_header(
    run_metadata: RunMetadata,
    clean_listings: list[NormalizedListing],
    flagged_listings: list[NormalizedListing],
) -> str:
    ds = run_metadata.date_range_start
    de = run_metadata.date_range_end
    week_label = f"{ds.day} {ds.strftime('%B')} – {de.day} {de.strftime('%B')} {de.year}"

    ts = run_metadata.run_finished_at
    ts_label = f"{ts.strftime('%A')}, {ts.day} {ts.strftime('%B')} {ts.year} at {ts.strftime('%H:%M')} UTC"

    succeeded = ", ".join(run_metadata.sources_succeeded) or "none"
    premium_count = sum(1 for l in clean_listings if is_premium(l))

    failed_html = ""
    if run_metadata.sources_failed:
        failed_parts = [f"{_h(s)}: {_h(reason)}" for s, reason in run_metadata.sources_failed.items()]
        failed_html = f'<span class="sources-failed">❌ Failed: {", ".join(failed_parts)}</span>'

    header = f"""<header class="report-header">
  <h1>Weekly Mechanical Engineering PM Contract Report</h1>
  <div class="report-meta">
    <span><strong>Week of:</strong> {_h(week_label)}</span>
    <span><strong>Generated:</strong> {_h(ts_label)}</span>
    <span><strong>Sources:</strong> {_h(succeeded)}</span>
    <span><strong>Records:</strong> {run_metadata.total_after_dedup} unique (from {run_metadata.total_raw} raw)</span>
    {failed_html}
  </div>
</header>"""

    # Summary tiles — clickable filters.
    tile_defs = [
        (str(run_metadata.total_new), "New This Week", "new"),
        (str(run_metadata.total_urgent), "Urgent Starts ≤14 days", "urgent"),
        (str(premium_count), "Premium Rate (≥£700/day)", "premium"),
        (str(run_metadata.total_sanity_flagged), "Under Review", "flagged"),
        (str(run_metadata.total_after_dedup), "Total in Pipeline", "all"),
    ]
    tile_html = "\n".join(
        f'<div class="summary-tile" data-filter="{filt}" tabindex="0" role="button"'
        f' aria-label="Filter: {_h(l)}">'
        f'<div class="count">{_h(c)}</div><div class="label">{_h(l)}</div></div>'
        for c, l, filt in tile_defs
    )
    summary = f'<div class="summary-grid">\n{tile_html}\n</div>'

    return header + "\n" + summary


def _render_html_new_section(new_listings: list[NormalizedListing], today: date) -> str:
    count = len(new_listings)
    heading = f"🆕 New This Week"
    body: list[str] = []
    if not new_listings:
        body.append('<p class="empty-section">No new listings this run.</p>')
    else:
        sorted_new = sorted(
            new_listings,
            key=lambda l: (l.start_date is None, l.start_date or date.max, -(effective_day_rate(l) or 0)),
        )
        for listing in sorted_new:
            body.append(_role_card(listing, today, "card-new", _SUMMARY_MAX_CHARS))

    return f"""<section id="new-listings">
  <header>
    <h2>{heading}</h2>
    <span class="section-count">{count} role{"s" if count != 1 else ""}</span>
  </header>
  {"".join(body)}
</section>"""


def _render_html_premium_section(premium_listings: list[NormalizedListing], today: date) -> str:
    if not premium_listings:
        return ""
    count = len(premium_listings)
    sorted_p = sorted(
        premium_listings,
        key=lambda l: -(effective_day_rate(l) or 0),
    )
    body = "".join(_role_card(l, today, "card-premium", _SUMMARY_MAX_CHARS) for l in sorted_p)
    return f"""<section id="premium-rate">
  <header>
    <h2>💰 Premium Rate</h2>
    <span class="section-count">{count} role{"s" if count != 1 else ""} ≥£700/day</span>
  </header>
  <p class="section-note">Top-quartile day rates for UK mechanical engineering PM contracts.</p>
  {body}
</section>"""


def _render_html_urgent_section(urgent_listings: list[NormalizedListing], today: date) -> str:
    if not urgent_listings:
        return ""
    count = len(urgent_listings)
    cutoff_dt = today + timedelta(days=14)
    cutoff_label = f"{cutoff_dt.day} {cutoff_dt.strftime('%B')}"
    sorted_u = sorted(urgent_listings, key=lambda l: (l.start_date is None, l.start_date or date.max))
    body = "".join(_role_card(l, today, "card-urgent", _SUMMARY_COMPACT_CHARS) for l in sorted_u)
    return f"""<section id="urgent-starts">
  <header>
    <h2>⚡ Urgent Starts</h2>
    <span class="section-count">Starting by {_h(cutoff_label)} — {count} role{"s" if count != 1 else ""}</span>
  </header>
  {body}
</section>"""


def _render_html_pipeline_section(by_region: dict[str, list[NormalizedListing]], today: date) -> str:
    total = sum(len(v) for v in by_region.values())
    regions_html: list[str] = []

    for region in REGION_ORDER:
        region_listings = by_region.get(region)
        if not region_listings:
            continue

        region_label = _REGION_FLAGS.get(region, f"🇬🇧 {region}")
        tiers: dict[str, list[NormalizedListing]] = {"senior": [], "mid": [], "junior": []}
        for listing in region_listings:
            tiers[_classify_seniority(listing)].append(listing)

        tiers_html: list[str] = []
        for tier_key in ("senior", "mid", "junior"):
            tier_listings = tiers[tier_key]
            if not tier_listings:
                continue
            tier_listings.sort(
                key=lambda l: (l.start_date is None, l.start_date or date.max, -(effective_day_rate(l) or 0))
            )
            cards = "".join(_role_card(l, today, "card-pipeline", _SUMMARY_COMPACT_CHARS) for l in tier_listings)
            tiers_html.append(
                f'<h4 class="seniority-heading">{_h(_SENIORITY_LABELS[tier_key])}</h4>'
                + cards
            )

        regions_html.append(
            f'<div class="region-section">'
            f'<h3 class="region-heading">{_h(region_label)}'
            f' <small>({len(region_listings)} role{"s" if len(region_listings) != 1 else ""})</small></h3>'
            + "".join(tiers_html)
            + "</div>"
        )

    return f"""<section id="pipeline">
  <header>
    <h2>All Current Roles — By Region</h2>
    <span class="section-count">{total} active role{"s" if total != 1 else ""} across UK</span>
  </header>
  <p class="section-note">Listed by region, then seniority (descending), then start date (ascending).</p>
  {"".join(regions_html)}
</section>"""


def _render_html_review_queue(flagged_listings: list[NormalizedListing], today: date) -> str:
    if not flagged_listings:
        return ""
    count = len(flagged_listings)
    body_parts: list[str] = []
    for listing in flagged_listings:
        reasons = get_sanity_reasons(listing, today)
        body_parts.append(_role_card(listing, today, "card-review", _SUMMARY_COMPACT_CHARS, sanity_reasons=reasons))

    return f"""<section id="review-queue">
  <header>
    <h2>⚠️ Review Queue</h2>
    <span class="section-count">{count} role{"s" if count != 1 else ""}</span>
  </header>
  <p class="section-note">
    These listings carry a geographic-uncertainty flag and require manual review.
    Rate-missing and unrecognised-location roles appear in the main section above with a soft note —
    consistent with UK contract market norms.
  </p>
  {"".join(body_parts)}
</section>"""


def _render_html_data_quality(run_metadata: RunMetadata, num_flagged: int) -> str:
    source_items: list[str] = []
    for src in run_metadata.sources_succeeded:
        source_items.append(f'<li class="src-ok">✅ {_h(src)}</li>')
    for src, reason in run_metadata.sources_failed.items():
        source_items.append(f'<li class="src-fail">❌ {_h(src)}: {_h(reason)}</li>')

    clean_run = (
        '<p>✅ No sanity flags this run — all records passed automated checks.</p>'
        if num_flagged == 0
        else ""
    )
    de = run_metadata.date_range_end
    freshness = f"{de.day} {de.strftime('%B')} {de.year}"
    pipeline_str = (
        f"{run_metadata.total_raw} raw → {run_metadata.total_after_filter} filtered"
        f" → {run_metadata.total_after_dedup} deduplicated"
    )

    return f"""<div class="data-quality-box">
  <h3>🔍 Data Quality &amp; Notes</h3>
  {clean_run}
  <p><strong>Source coverage:</strong></p>
  <ul>{"".join(source_items)}</ul>
  <p style="margin-top:8px"><strong>Data freshness:</strong> All listings verified active as of {_h(freshness)}.</p>
  <p><strong>Pipeline:</strong> {_h(pipeline_str)}</p>
</div>"""


def _render_html_footer(run_metadata: RunMetadata, total: int, generated_at: datetime) -> str:
    next_report = _next_friday_from(date.today())
    next_label = f"{next_report.day} {next_report.strftime('%B')} {next_report.year}"
    ts_str = f"{generated_at.day} {generated_at.strftime('%B')} {generated_at.year} at {generated_at.strftime('%H:%M')} UTC"

    return f"""<footer>
  <p>
    <strong>Next Report:</strong> Friday, {_h(next_label)}&emsp;|&emsp;
    <strong>Total listings:</strong> {total}&emsp;|&emsp;
    Generated {_h(ts_str)}
  </p>
  <p style="margin-top:6px">Generated by Mech-PM-Finder &mdash; Report format &amp; domain notes by Polly (Reporting &amp; Domain).</p>
</footer>"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_weekly_html(
    listings: list[NormalizedListing],
    run_metadata: RunMetadata,
    output_path: Path,
) -> Path:
    """Render a weekly HTML report and write it to *output_path*.

    Produces a single self-contained HTML file (CSS embedded, no external assets)
    suitable for opening locally in a browser.  All links are clickable.

    Args:
        listings:      All NormalizedListing objects for this run, including
                       sanity-flagged ones.  The renderer partitions them internally.
        run_metadata:  Pipeline provenance and count metrics.
        output_path:   Destination ``.html`` file.  Parent dirs are created if absent.

    Returns:
        Resolved absolute path of the written file.
    """
    today = run_metadata.date_range_end
    generated_at = run_metadata.run_finished_at

    # Partition — mirrors render.py logic exactly.
    flagged = [l for l in listings if is_geo_flagged(l)]
    clean = [l for l in listings if not is_geo_flagged(l)]

    new_listings = [l for l in clean if l.is_new_listing]
    urgent_listings = [l for l in clean if is_urgent(l, today)]
    premium_listings = [l for l in clean if is_premium(l)]
    by_region = group_by_region(clean)

    # Build document body.
    body_parts: list[str] = [
        _render_html_header(run_metadata, clean, flagged),
        _render_combined_filter_bar(listings),
        _render_html_new_section(new_listings, today),
        _render_html_premium_section(premium_listings, today),
        _render_html_urgent_section(urgent_listings, today),
        _render_html_pipeline_section(by_region, today),
        _render_html_review_queue(flagged, today),
        _render_html_data_quality(run_metadata, len(flagged)),
        _render_html_footer(run_metadata, len(clean), generated_at),
    ]

    body_html = "\n".join(p for p in body_parts if p)

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mech PM Finder — {_h(str(run_metadata.date_range_end))}</title>
  <style>
{_CSS}
  </style>
</head>
<body>
<div class="report-wrap">
{body_html}
</div>
{_FILTER_JS}
</body>
</html>"""

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")

    return output_path
