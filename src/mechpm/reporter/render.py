"""Report renderer — converts NormalizedListing objects to a Markdown report.

Matches the format established in ``reports/2026-06-12-SAMPLE.md``.

Section order:
    1. Header (date range, run metadata, source coverage, totals)
    2. Summary block
    3. 🆕 New This Week
    4. ⚡ Urgent Starts (≤ 14 days)
    5. All Current Roles — By Region (grouped, with seniority sub-sections)
    6. ⚠️ Review Queue (sanity-flagged listings, never hidden)
    7. Footer
"""
from __future__ import annotations

import textwrap
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

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
)
from mechpm.reporter.models import RunMetadata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUMMARY_MAX_CHARS = 320  # truncation length for description in full cards
_SUMMARY_COMPACT_CHARS = 160  # compact cards (urgent / pipeline)

_SENIORITY_LABELS: dict[str, str] = {
    "senior":    "Senior / Programme Level",
    "mid":       "Mid-Level",
    "junior":    "Junior / Coordinator Level",
}

_REGION_FLAGS: dict[str, str] = {
    "London":     "🇬🇧 London",
    "South-East": "🇬🇧 South-East",
    "Midlands":   "🇬🇧 Midlands",
    "North":      "🇬🇧 North",
    "Scotland":   "🏴󠁧󠁢󠁳󠁣󠁴󠁿 Scotland",
    "Wales":      "🏴󠁧󠁢󠁷󠁬󠁳󠁿 Wales",
    "Remote":     "🌐 Remote / UK-Wide",
    "Other":      "🇬🇧 Other UK",
    "Region TBC": "🔍 Region TBC",
}

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _next_friday_from(today: date) -> date:
    """Return the next Friday strictly after *today*.

    If *today* is already a Friday, returns the Friday 7 days hence so
    the footer always points to the *next* scheduled run, not the current one.

    >>> from datetime import date
    >>> _next_friday_from(date(2026, 6, 16))  # Tuesday → Friday same week
    datetime.date(2026, 6, 19)
    >>> _next_friday_from(date(2026, 6, 19))  # Friday → next Friday
    datetime.date(2026, 6, 26)
    """
    days_until_friday = (4 - today.weekday()) % 7  # Friday == weekday 4
    if days_until_friday == 0:
        days_until_friday = 7
    return today + timedelta(days=days_until_friday)


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

_MD_SPECIAL = r'\*_[]|`~'


def _md_escape(text: str) -> str:
    """Escape Markdown special characters in plain-text content."""
    for char in _MD_SPECIAL:
        text = text.replace(char, '\\' + char)
    return text


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return textwrap.shorten(text, width=max_chars, placeholder="…")


# ---------------------------------------------------------------------------
# Rate / IR35 / duration formatters
# ---------------------------------------------------------------------------

def _rate_str(listing: NormalizedListing) -> str:
    lo, hi = listing.day_rate_min, listing.day_rate_max
    if lo is None and hi is None:
        return "Rate TBC"
    unit = "/hr" if listing.rate_period == "hour" else "/day"
    if lo and hi and lo != hi:
        return f"£{lo:,.0f}–£{hi:,.0f}{unit}"
    rate = hi or lo
    return f"£{rate:,.0f}{unit}"


def _ir35_badge(ir35: str | None) -> str:
    if ir35 == "outside":
        return "Outside ✅"
    if ir35 == "inside":
        return "Inside 🏢"
    return "⚠️ Not stated"


def _duration_str(duration_weeks: int | None, abbreviate: bool = False) -> str:
    if duration_weeks is None:
        return "Duration TBC"
    if abbreviate:
        if duration_weeks % 4 == 0:
            return f"{duration_weeks // 4}m"
        return f"{duration_weeks}w"
    if duration_weeks % 4 == 0:
        months = duration_weeks // 4
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{duration_weeks} week{'s' if duration_weeks != 1 else ''}"


def _start_str(start_date: date | None, today: date, abbreviate: bool = False) -> str:
    if start_date is None:
        return "Start TBC"
    delta = (start_date - today).days
    if abbreviate:
        day_label = start_date.strftime("%-d %b") if hasattr(start_date, "strftime") else str(start_date)
        if delta < 0:
            return f"{day_label} ⚠️ PAST"
        return f"{day_label} ({delta} days)"
    day_label = start_date.strftime("%-d %B %Y") if hasattr(start_date, "strftime") else str(start_date)
    if delta < 0:
        return f"{day_label} (**⚠️ PAST — {abs(delta)} days ago**)"
    if delta == 0:
        return f"{day_label} (**today!**)"
    return f"{day_label} ({delta} days)"


def _start_str_safe(start_date: date | None, today: date, abbreviate: bool = False) -> str:
    """Cross-platform date formatting (%-d not available on Windows)."""
    if start_date is None:
        return "Start TBC"
    delta = (start_date - today).days
    day = start_date.day
    month_abbr = start_date.strftime("%b")
    month_full = start_date.strftime("%B")
    year = start_date.year

    if abbreviate:
        label = f"{day} {month_abbr}"
        if delta < 0:
            return f"{label} ⚠️ PAST"
        return f"{label} ({delta} days)"

    label = f"{day} {month_full} {year}"
    if delta < 0:
        return f"{label} (**⚠️ PAST — {abs(delta)} days ago**)"
    if delta == 0:
        return f"{label} (**today!**)"
    return f"{label} ({delta} days)"


def _discovered_str(discovered_at: datetime) -> str:
    return discovered_at.strftime("%-d %B %Y") if False else (
        f"{discovered_at.day} {discovered_at.strftime('%B')} {discovered_at.year}"
    )


# ---------------------------------------------------------------------------
# Source display helpers
# ---------------------------------------------------------------------------

_SOURCE_URL_MAP: dict[str, str] = {
    "reed.co.uk":             "Reed",
    "cwjobs.co.uk":           "CWJobs",
    "totaljobs.com":          "Totaljobs",
    "linkedin.com":           "LinkedIn",
    "railwaypeople.com":      "RailwayPeople",
    "energyjobline.com":      "Energy Jobline",
    "theengineer.co.uk":      "The Engineer Jobs",
    "aviationjobsearch.com":  "Aviation Job Search",
    "apsco.org":              "APSCo",
}


def _source_name_from_url(url: str) -> str:
    """Infer a human-readable source name from a URL."""
    try:
        netloc = urlparse(url).netloc.lower().removeprefix("www.")
        for domain, name in _SOURCE_URL_MAP.items():
            if domain in netloc:
                return name
        return netloc.split(".")[0].title()
    except Exception:
        return "Source"


def _employer_line(listing: NormalizedListing) -> str:
    """Return 'Employer — Title' heading text, preferring employer over agency."""
    name = _md_escape(listing.employer or listing.agency or "Unknown Employer")
    title = _md_escape(listing.title)
    return f"{name} — {title}"


def _flags_str(listing: NormalizedListing, today: date) -> str:
    """Build the emoji flag string for a listing."""
    parts: list[str] = []
    if listing.is_new_listing:
        parts.append("🆕")
    if is_urgent(listing, today):
        parts.append("⚡")
    if is_premium(listing):
        parts.append("💰")
    if is_sanity_flagged(listing, today):
        parts.append("⚠️")
    return " ".join(parts)


def _source_links(listing: NormalizedListing, label: str = "View Listing") -> str:
    """Render one or more source hyperlinks."""
    if not listing.source_urls:
        return f"*Source: {_md_escape(listing.source)}*"
    if len(listing.source_urls) == 1:
        name = _source_name_from_url(listing.source_urls[0])
        return f"**Source:** {name} | [{label}]({listing.source_urls[0]})"
    parts = []
    for url in listing.source_urls:
        name = _source_name_from_url(url)
        parts.append(f"[{name}]({url})")
    return "**Sources:** " + " · ".join(parts)


# ---------------------------------------------------------------------------
# Card renderers — one per report section
# ---------------------------------------------------------------------------

def _render_full_card(listing: NormalizedListing, today: date) -> list[str]:
    """Full listing card for the 🆕 New This Week section."""
    lines: list[str] = []
    flags = _flags_str(listing, today)
    flag_loc = f"{flags} | {_md_escape(listing.location)}" if flags else _md_escape(listing.location)

    lines.append(f"### {_employer_line(listing)}")
    lines.append("")
    lines.append(flag_loc)
    lines.append("")
    lines.append(
        f"- **Day Rate:** {_rate_str(listing)} | **IR35:** {_ir35_badge(listing.ir35_status)}"
        f" | {rate_context(listing)}"
    )
    lines.append(
        f"- **Duration:** {_duration_str(listing.duration_weeks)}"
        f" | **Start:** {_start_str_safe(listing.start_date, today)}"
    )
    if listing.description_clean:
        summary = _md_escape(_truncate(listing.description_clean, _SUMMARY_MAX_CHARS))
        lines.append(f"- **Summary:** {summary}")
    lines.append(
        f"- **Discovered:** {_discovered_str(listing.discovered_at)}"
        f" | {_source_links(listing, 'View Full Listing')}"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _render_compact_card(listing: NormalizedListing, today: date) -> list[str]:
    """Compact card for the ⚡ Urgent Starts section."""
    lines: list[str] = []
    flags = _flags_str(listing, today)
    flag_loc = f"{flags} | {_md_escape(listing.location)}" if flags else _md_escape(listing.location)

    lines.append(f"### {_employer_line(listing)}")
    lines.append("")
    lines.append(flag_loc)
    lines.append(
        f"- **Day Rate:** {_rate_str(listing)}"
        f" | **IR35:** {_ir35_badge(listing.ir35_status)}"
        f" | **Start:** {_start_str_safe(listing.start_date, today, abbreviate=True)}"
        f" | **Duration:** {_duration_str(listing.duration_weeks, abbreviate=True)}"
    )
    if listing.description_clean:
        summary = _md_escape(_truncate(listing.description_clean, _SUMMARY_COMPACT_CHARS))
        lines.append(f"- **Summary:** {summary}")
    lines.append(f"- {_source_links(listing)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _render_pipeline_card(listing: NormalizedListing, today: date) -> list[str]:
    """Medium-detail card for the All Current Roles pipeline section."""
    lines: list[str] = []
    flags = _flags_str(listing, today)
    flag_suffix = f" {flags}" if flags else ""

    lines.append(f"**{_employer_line(listing)}**{flag_suffix}")
    lines.append(f"| {_md_escape(listing.location)}")
    lines.append(
        f"- **Day Rate:** {_rate_str(listing)}"
        f" | **IR35:** {_ir35_badge(listing.ir35_status)}"
        f" | **Duration:** {_duration_str(listing.duration_weeks, abbreviate=True)}"
        f" | **Start:** {_start_str_safe(listing.start_date, today, abbreviate=True)}"
    )
    if listing.description_clean:
        summary = _md_escape(_truncate(listing.description_clean, _SUMMARY_COMPACT_CHARS))
        lines.append(f"- **Summary:** {summary}")
    soft_notes = get_soft_notes(listing)
    if soft_notes:
        lines.append(f"- 💡 {'; '.join(soft_notes)}")
    lines.append(f"- {_source_links(listing)}")
    lines.append("")
    return lines


def _render_review_card(listing: NormalizedListing, today: date, reasons: list[str]) -> list[str]:
    """Card for the ⚠️ Review Queue section — always shows why it was flagged."""
    lines: list[str] = []
    lines.append(f"### {_employer_line(listing)} ⚠️")
    lines.append("")
    lines.append(f"| {_md_escape(listing.location)}")
    lines.append(
        f"- **Day Rate:** {_rate_str(listing)}"
        f" | **IR35:** {_ir35_badge(listing.ir35_status)}"
    )
    lines.append(
        f"- **Duration:** {_duration_str(listing.duration_weeks)}"
        f" | **Start:** {_start_str_safe(listing.start_date, today)}"
    )
    reason_list = "; ".join(reasons) if reasons else "see pipeline flags"
    lines.append(f"- **⚠️ Flagged because:** {_md_escape(reason_list)}")
    lines.append(f"- {_source_links(listing)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_header(
    run_metadata: RunMetadata,
    clean_listings: list[NormalizedListing],
    flagged_listings: list[NormalizedListing],
) -> list[str]:
    lines: list[str] = []

    # Build source coverage line.
    succeeded = ", ".join(run_metadata.sources_succeeded) or "none"
    failed_note = ""
    if run_metadata.sources_failed:
        failed_parts = [f"{s} (⚠️ {reason})" for s, reason in run_metadata.sources_failed.items()]
        failed_note = f"  ❌ **Failed sources:** {', '.join(failed_parts)}"

    # Date range.
    ds = run_metadata.date_range_start
    de = run_metadata.date_range_end
    week_label = (
        f"{ds.day} {ds.strftime('%B')} – {de.day} {de.strftime('%B')} {de.year}"
    )

    ts = run_metadata.run_finished_at
    ts_label = (
        f"{ts.strftime('%A')}, {ts.day} {ts.strftime('%B')} {ts.year}"
        f" at {ts.strftime('%H:%M')} UTC"
    )

    premium_count = sum(1 for l in clean_listings if is_premium(l))

    lines += [
        "# Weekly Mechanical Engineering PM Contract Report",
        "",
        f"**Week of:** {week_label}  ",
        f"**Report Generated:** {ts_label}  ",
        f"**Data Coverage:** {succeeded}  ",
        f"**Deduplicated Records:** {run_metadata.total_after_dedup} unique active roles"
        f" (scanned from {run_metadata.total_raw} source postings)",
    ]
    if failed_note:
        lines.append(failed_note)

    lines += [
        "",
        "---",
        "",
        "## Summary",
        "",
        f"- **New this week:** {run_metadata.total_new} roles",
        f"- **Urgent starts (≤14 days):** {run_metadata.total_urgent} roles ⚡",
        f"- **Premium rate roles (≥£700/day):** {premium_count} roles 💰",
        f"- **Under review (sanity flags):** {run_metadata.total_sanity_flagged}"
        f" role{'s' if run_metadata.total_sanity_flagged != 1 else ''} ⚠️",
        f"- **Pipeline:** {run_metadata.total_after_filter} passed filters"
        f" → {run_metadata.total_after_dedup} after dedup",
        "",
        "---",
        "",
    ]
    return lines


def _render_new_section(new_listings: list[NormalizedListing], today: date) -> list[str]:
    lines: list[str] = [
        f"## 🆕 New This Week ({len(new_listings)} role{'s' if len(new_listings) != 1 else ''})",
        "",
    ]
    if not new_listings:
        lines += ["*No new listings this run.*", "", "---", ""]
        return lines

    # Sort by start_date ascending (None last), then rate descending.
    sorted_new = sorted(
        new_listings,
        key=lambda l: (
            l.start_date is None,
            l.start_date or date.max,
            -(effective_day_rate(l) or 0),
        ),
    )
    for listing in sorted_new:
        lines.extend(_render_full_card(listing, today))
    return lines


def _render_urgent_section(urgent_listings: list[NormalizedListing], today: date) -> list[str]:
    if not urgent_listings:
        return []

    # Sort by start_date ascending.
    sorted_urgent = sorted(
        urgent_listings,
        key=lambda l: (l.start_date is None, l.start_date or date.max),
    )

    cutoff = today.replace(day=today.day + 14) if False else date(
        today.year, today.month, today.day
    )
    # Compute the cutoff label (today + 14 days).
    from datetime import timedelta
    cutoff_dt = today + timedelta(days=14)
    cutoff_label = f"{cutoff_dt.day} {cutoff_dt.strftime('%B')}"

    lines: list[str] = [
        f"## ⚡ Urgent Starts (Starting by {cutoff_label} — {len(sorted_urgent)} role{'s' if len(sorted_urgent) != 1 else ''})",
        "",
    ]
    for listing in sorted_urgent:
        lines.extend(_render_compact_card(listing, today))
    return lines


def _classify_seniority(listing: NormalizedListing) -> str:
    """Map listing to a display seniority tier for pipeline sub-sections."""
    rate = effective_day_rate(listing) or 0
    if rate >= 700:
        return "senior"
    if rate >= 480:
        return "mid"
    return "junior"


def _render_pipeline_section(
    by_region: dict[str, list[NormalizedListing]],
    today: date,
) -> list[str]:
    total = sum(len(v) for v in by_region.values())
    lines: list[str] = [
        f"## All Current Roles — By Region",
        "",
        f"*{total} active role{'s' if total != 1 else ''} across UK."
        " Listed by region, then seniority (descending), then start date (ascending).*",
        "",
    ]

    for region in REGION_ORDER:
        region_listings = by_region.get(region)
        if not region_listings:
            continue

        heading = _REGION_FLAGS.get(region, f"🇬🇧 {region}")
        lines.append(f"### {heading} ({len(region_listings)} role{'s' if len(region_listings) != 1 else ''})")
        lines.append("")

        # Group into seniority tiers.
        tiers: dict[str, list[NormalizedListing]] = {"senior": [], "mid": [], "junior": []}
        for listing in region_listings:
            tiers[_classify_seniority(listing)].append(listing)

        for tier_key in ("senior", "mid", "junior"):
            tier_listings = tiers[tier_key]
            if not tier_listings:
                continue

            lines.append(f"#### {_SENIORITY_LABELS[tier_key]}")
            lines.append("")

            # Sort: start_date asc (None last), then rate desc.
            tier_listings.sort(
                key=lambda l: (
                    l.start_date is None,
                    l.start_date or date.max,
                    -(effective_day_rate(l) or 0),
                )
            )
            for listing in tier_listings:
                lines.extend(_render_pipeline_card(listing, today))

        lines.append("---")
        lines.append("")

    return lines


def _render_review_queue(
    flagged_listings: list[NormalizedListing],
    today: date,
) -> list[str]:
    """Render ⚠️ Review Queue section.  Returns empty list when nothing is flagged
    (the footer's Data Quality section then notes the clean run instead)."""
    if not flagged_listings:
        return []

    lines: list[str] = [
        f"## ⚠️ Review Queue ({len(flagged_listings)} role{'s' if len(flagged_listings) != 1 else ''})",
        "",
        "*These listings carry a geographic-uncertainty flag and require manual review."
        " Rate-missing and unrecognised-location roles appear in the main section above"
        " with a 💡 soft note — consistent with UK contract market norms.*",
        "",
    ]
    for listing in flagged_listings:
        reasons = get_sanity_reasons(listing, today)
        lines.extend(_render_review_card(listing, today, reasons))

    return lines


def _render_footer(run_metadata: RunMetadata, num_flagged: int = 0) -> list[str]:
    next_report = _next_friday_from(date.today())
    next_label = f"{next_report.day} {next_report.strftime('%B')} {next_report.year}"

    # Source breakdown.
    source_lines: list[str] = []
    for src in run_metadata.sources_succeeded:
        source_lines.append(f"  - {src}: included ✅")
    for src, reason in run_metadata.sources_failed.items():
        source_lines.append(f"  - {src}: ❌ {reason}")

    lines: list[str] = [
        "## 🔍 Data Quality & Notes",
        "",
    ]
    if num_flagged == 0:
        lines.append("- **No ⚠️ sanity flags this run:** All records passed automated checks.")
    lines += [
        "- **Source coverage:**",
    ]
    lines.extend(source_lines)
    lines += [
        f"- **Data freshness:** All listings verified active as of"
        f" {run_metadata.date_range_end.day}"
        f" {run_metadata.date_range_end.strftime('%B')} {run_metadata.date_range_end.year}.",
        f"- **Pipeline:** {run_metadata.total_raw} raw → {run_metadata.total_after_filter}"
        f" filtered → {run_metadata.total_after_dedup} deduplicated.",
        "",
        "---",
        "",
        f"**Next Report:** Friday, {next_label}  ",
        "**Questions or feedback?** Contact Polly (domain + report design)",
        "",
        "---",
        "",
        "*Generated by Mech-PM-Finder. Report format & domain notes by Polly (Reporting & Domain).*",
    ]
    return lines


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_weekly(
    listings: list[NormalizedListing],
    run_metadata: RunMetadata,
    output_path: Path,
) -> Path:
    """Render a weekly Markdown report and write it to *output_path*.

    Args:
        listings:      All NormalizedListing objects for this run, including
                       sanity-flagged ones.  The renderer partitions them
                       internally.
        run_metadata:  Pipeline provenance and count metrics.
        output_path:   Destination ``.md`` file.  Parent directories are
                       created if they do not exist.

    Returns:
        The resolved absolute path of the written file.
    """
    today = run_metadata.date_range_end

    # Partition: only geo-uncertain listings go to the Review Queue.
    # Rate-missing and location-vague observations are soft notes in the main
    # sections — consistent with UK contract market norms (most roles negotiate
    # rate at offer stage; bare postcodes are perfectly valid UK locations).
    flagged = [l for l in listings if is_geo_flagged(l)]
    clean = [l for l in listings if not is_geo_flagged(l)]

    new_listings = [l for l in clean if l.is_new_listing]
    urgent_listings = [l for l in clean if is_urgent(l, today)]
    by_region = group_by_region(clean)

    sections: list[list[str]] = [
        _render_header(run_metadata, clean, flagged),
        _render_new_section(new_listings, today),
        _render_urgent_section(urgent_listings, today),
        _render_pipeline_section(by_region, today),
        _render_review_queue(flagged, today),
        _render_footer(run_metadata, num_flagged=len(flagged)),
    ]

    content = "\n".join(line for section in sections for line in section)

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return output_path
