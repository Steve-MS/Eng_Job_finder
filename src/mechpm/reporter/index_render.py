"""Index page generator for the reports directory.

Scans ``reports/`` for dated HTML reports, generates:
  - ``reports/index.html`` — archive page listing all reports (newest first)
  - ``reports/latest.html`` — redirect to the most recent report

Can be run standalone:  ``python -m mechpm.reporter.index_render``
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")

_REPORTS_DIR = Path("reports")

# Re-use the same CSS variables and base styles from the main reports.
_INDEX_CSS = """\
:root {
    --bg:           #f7f8fa;
    --card-bg:      #ffffff;
    --border:       #e2e5ea;
    --accent:       #1a56db;
    --accent-hover: #1040b0;
    --text:         #1a1d23;
    --muted:        #6b7280;
    --section-h:    #0f172a;
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
.wrap { max-width: 720px; margin: 0 auto; }
.header {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 24px 28px;
    margin-bottom: 24px;
}
.header h1 { font-size: 22px; font-weight: 700; color: var(--section-h); margin-bottom: 4px; }
.header p { color: var(--muted); font-size: 13px; }
.latest-callout {
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 24px;
}
.latest-callout a {
    font-size: 16px;
    font-weight: 600;
    color: var(--accent);
    text-decoration: none;
}
.latest-callout a:hover { text-decoration: underline; color: var(--accent-hover); }
.latest-callout .date { color: var(--muted); font-size: 13px; margin-top: 4px; }
.report-list { list-style: none; }
.report-list li {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 18px;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.report-list li a {
    color: var(--accent);
    text-decoration: none;
    font-weight: 500;
}
.report-list li a:hover { text-decoration: underline; }
.report-list li .meta { color: var(--muted); font-size: 12px; }
"""


def _discover_reports(reports_dir: Path) -> list[str]:
    """Return sorted list of YYYY-MM-DD date strings for existing HTML reports (newest first)."""
    dates: list[str] = []
    if not reports_dir.exists():
        return dates
    for f in reports_dir.iterdir():
        m = _DATE_RE.match(f.name)
        if m:
            dates.append(m.group(1))
    dates.sort(reverse=True)
    return dates


def _format_date_label(date_str: str) -> str:
    """Pretty format: 'Friday 15 Jun 2026'."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%A %-d %b %Y")
    except (ValueError, AttributeError):
        # %-d not supported on Windows — fall back
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            return d.strftime("%A %d %b %Y").replace(" 0", " ")
        except ValueError:
            return date_str


def generate_index(reports_dir: Path | None = None) -> Path:
    """Generate ``index.html`` and ``latest.html`` in the reports directory.

    Returns the path to ``index.html``.
    """
    reports_dir = reports_dir or _REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)

    dates = _discover_reports(reports_dir)

    latest_date = dates[0] if dates else None

    # --- Build index.html ---
    lines: list[str] = []
    lines.append("<!DOCTYPE html>")
    lines.append('<html lang="en">')
    lines.append("<head>")
    lines.append('  <meta charset="UTF-8">')
    lines.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append("  <title>Mech PM Finder — Report Archive</title>")
    lines.append(f"  <style>{_INDEX_CSS}</style>")
    lines.append("</head>")
    lines.append("<body>")
    lines.append('<div class="wrap">')

    # Header
    lines.append('<div class="header">')
    lines.append("  <h1>Mech PM Finder — Report Archive</h1>")
    if latest_date:
        lines.append(f'  <p>Last updated: {_format_date_label(latest_date)}</p>')
    else:
        lines.append("  <p>No reports generated yet. Run the pipeline to create your first report.</p>")
    lines.append("</div>")

    # Latest callout
    if latest_date:
        lines.append('<div class="latest-callout">')
        lines.append(f'  <a href="{latest_date}.html">📋 Latest Report — {_format_date_label(latest_date)}</a>')
        lines.append(f'  <div class="date">Also available at <a href="latest.html">latest.html</a> (stable bookmark URL)</div>')
        lines.append("</div>")

    # Report list
    if dates:
        lines.append('<ul class="report-list">')
        for i, d in enumerate(dates):
            label = _format_date_label(d)
            badge = ' <span style="color:#2563eb;font-weight:600;">[Latest]</span>' if i == 0 else ""
            lines.append(f'  <li><a href="{d}.html">{label}</a>{badge}<span class="meta">{d}</span></li>')
        lines.append("</ul>")
    else:
        lines.append("<p>No reports found.</p>")

    lines.append("</div>")
    lines.append("</body>")
    lines.append("</html>")

    index_path = reports_dir / "index.html"
    index_path.write_text("\n".join(lines), encoding="utf-8")

    # --- Build latest.html (meta-redirect to newest report) ---
    latest_path = reports_dir / "latest.html"
    if latest_date:
        redirect_html = (
            "<!DOCTYPE html>\n"
            "<html>\n"
            "<head>\n"
            f'  <meta http-equiv="refresh" content="0; url={latest_date}.html">\n'
            f"  <title>Redirecting to latest report...</title>\n"
            "</head>\n"
            "<body>\n"
            f'  <p>Redirecting to <a href="{latest_date}.html">{latest_date}.html</a>...</p>\n'
            "</body>\n"
            "</html>"
        )
        latest_path.write_text(redirect_html, encoding="utf-8")
    else:
        latest_path.write_text(
            "<!DOCTYPE html><html><body><p>No reports generated yet.</p></body></html>",
            encoding="utf-8",
        )

    return index_path


if __name__ == "__main__":
    path = generate_index()
    print(f"Generated: {path}")
    print(f"Generated: {path.parent / 'latest.html'}")
