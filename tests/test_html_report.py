"""tests/test_html_report.py — Tests for the HTML report renderer.

Validates:
  1. Every role card has at least one <a href> pointing to the listing's source URL.
  2. IR35 badges carry the expected CSS classes.
  3. Premium-rate listings appear in the premium-rate section.
  4. Output is byte-identical across two renders (deterministic ordering).
  5. Multiple source URLs (cross-source dedup) all appear as links.

Added 2026-06-15 — see .squad/decisions/inbox/polly-html-report.md.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from mechpm.models import NormalizedListing
from mechpm.reporter.html_render import render_weekly_html
from mechpm.reporter.models import RunMetadata


# ---------------------------------------------------------------------------
# Minimal link parser using stdlib html.parser (no lxml / BS4 needed)
# ---------------------------------------------------------------------------

class _LinkParser(HTMLParser):
    """Collects all href attribute values and class attribute values."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.classes: list[str] = []
        self.data_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if "href" in attr_dict and attr_dict["href"]:
            self.hrefs.append(attr_dict["href"])
        if "class" in attr_dict and attr_dict["class"]:
            self.classes.extend(attr_dict["class"].split())

    def handle_data(self, data: str) -> None:
        self.data_parts.append(data)


def _parse_html(content: str) -> _LinkParser:
    parser = _LinkParser()
    parser.feed(content)
    return parser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
_TODAY = date(2026, 6, 15)


def _make_listing(**kwargs) -> NormalizedListing:
    defaults: dict = {
        "source": "reed",
        "source_url": "https://www.reed.co.uk/jobs/pm/12345",
        "source_listing_id": "12345",
        "title": "Senior Project Manager – Mechanical",
        "employer": "Acme Engineering",
        "location": "Manchester",
        "location_normalized": "manchester",
        "day_rate_min": 600.0,
        "day_rate_max": 650.0,
        "rate_period": "day",
        "ir35_status": "outside",
        "duration_weeks": 26,
        "start_date": date(2026, 7, 14),
        "description_clean": "Leading mechanical contractor seeks an experienced PM.",
        "source_urls": ["https://www.reed.co.uk/jobs/pm/12345"],
        "discovered_at": _NOW,
        "last_seen_at": _NOW,
        "is_new_listing": True,
    }
    defaults.update(kwargs)
    return NormalizedListing(**defaults)


def _make_run_metadata(listings: list[NormalizedListing]) -> RunMetadata:
    from mechpm.reporter.grouping import is_geo_flagged, is_urgent

    return RunMetadata(
        run_started_at=_NOW,
        run_finished_at=_NOW,
        date_range_start=date(2026, 6, 8),
        date_range_end=_TODAY,
        sources_attempted=["reed"],
        sources_succeeded=["reed"],
        sources_failed={},
        total_raw=len(listings),
        total_after_filter=len(listings),
        total_after_dedup=len(listings),
        total_new=sum(1 for l in listings if l.is_new_listing),
        total_urgent=sum(1 for l in listings if is_urgent(l, _TODAY)),
        total_sanity_flagged=sum(1 for l in listings if is_geo_flagged(l)),
    )


# ---------------------------------------------------------------------------
# Test 1: every card has at least one <a href> to the listing's source URL
# ---------------------------------------------------------------------------

def test_every_card_has_source_link(tmp_path: Path) -> None:
    listing = _make_listing()
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    parser = _parse_html(content)
    assert "https://www.reed.co.uk/jobs/pm/12345" in parser.hrefs, (
        "source_url must appear as an <a href> in the HTML output"
    )


# ---------------------------------------------------------------------------
# Test 2: title link points to primary source URL
# ---------------------------------------------------------------------------

def test_title_is_linked_to_source_url(tmp_path: Path) -> None:
    url = "https://www.reed.co.uk/jobs/pm/99999"
    listing = _make_listing(
        source_url=url,
        source_urls=[url],
        source_listing_id="99999",
    )
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    # The url should appear in href attributes.
    parser = _parse_html(content)
    assert url in parser.hrefs


# ---------------------------------------------------------------------------
# Test 3: IR35 badges have correct classes
# ---------------------------------------------------------------------------

class TestIR35Badges:
    def _render(self, ir35: str | None, tmp_path: Path) -> str:
        listing = _make_listing(ir35_status=ir35)
        meta = _make_run_metadata([listing])
        out = render_weekly_html([listing], meta, tmp_path / f"report-{ir35}.html")
        return out.read_text(encoding="utf-8")

    def test_outside_ir35_badge(self, tmp_path: Path) -> None:
        content = self._render("outside", tmp_path)
        assert "ir35-outside" in content

    def test_inside_ir35_badge(self, tmp_path: Path) -> None:
        content = self._render("inside", tmp_path)
        assert "ir35-inside" in content

    def test_umbrella_ir35_badge(self, tmp_path: Path) -> None:
        content = self._render("umbrella", tmp_path)
        assert "ir35-umbrella" in content

    def test_not_stated_ir35_badge(self, tmp_path: Path) -> None:
        content = self._render(None, tmp_path)
        assert "ir35-none" in content

    def test_not_stated_string_ir35_badge(self, tmp_path: Path) -> None:
        content = self._render("not_stated", tmp_path)
        assert "ir35-none" in content


# ---------------------------------------------------------------------------
# Test 4: premium-rate listings appear in the premium section
# ---------------------------------------------------------------------------

def test_premium_listing_in_premium_section(tmp_path: Path) -> None:
    premium = _make_listing(
        source_listing_id="premium1",
        source_url="https://www.reed.co.uk/jobs/pm/premium1",
        source_urls=["https://www.reed.co.uk/jobs/pm/premium1"],
        day_rate_max=800.0,
        ir35_status="outside",
        title="Programme Director",
    )
    ordinary = _make_listing(
        source_listing_id="ordinary1",
        source_url="https://www.reed.co.uk/jobs/pm/ordinary1",
        source_urls=["https://www.reed.co.uk/jobs/pm/ordinary1"],
        day_rate_max=450.0,
        ir35_status="inside",
        title="Junior PM",
    )
    listings = [premium, ordinary]
    meta = _make_run_metadata(listings)
    out = render_weekly_html(listings, meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    # The premium section id should be present.
    assert 'id="premium-rate"' in content
    # The premium listing's URL should appear in the premium section (before pipeline section).
    premium_section_start = content.find('id="premium-rate"')
    pipeline_section_start = content.find('id="pipeline"')
    assert premium_section_start != -1
    assert pipeline_section_start != -1

    premium_url_pos = content.find("premium1", premium_section_start)
    assert 0 < premium_url_pos < pipeline_section_start, (
        "Premium listing URL must appear inside the premium-rate section"
    )


# ---------------------------------------------------------------------------
# Test 5: multiple source URLs all appear as links (cross-source dedup)
# ---------------------------------------------------------------------------

def test_multi_source_urls_all_clickable(tmp_path: Path) -> None:
    url1 = "https://www.reed.co.uk/jobs/pm/11111"
    url2 = "https://www.cwjobs.co.uk/jobs/pm/22222"
    listing = _make_listing(
        source_url=url1,
        source_urls=[url1, url2],
        source_listing_id="11111",
    )
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    parser = _parse_html(content)
    assert url1 in parser.hrefs, "First source URL must be a clickable link"
    assert url2 in parser.hrefs, "Second source URL must be a clickable link"


# ---------------------------------------------------------------------------
# Test 6: deterministic output — two renders produce byte-identical HTML
# ---------------------------------------------------------------------------

def test_render_is_deterministic(tmp_path: Path) -> None:
    listings = [
        _make_listing(source_listing_id="det1", source_url="https://www.reed.co.uk/jobs/pm/det1", source_urls=["https://www.reed.co.uk/jobs/pm/det1"]),
        _make_listing(source_listing_id="det2", source_url="https://www.reed.co.uk/jobs/pm/det2", source_urls=["https://www.reed.co.uk/jobs/pm/det2"], day_rate_max=750.0, ir35_status="outside"),
    ]
    meta = _make_run_metadata(listings)

    out1 = tmp_path / "run1.html"
    out2 = tmp_path / "run2.html"
    render_weekly_html(listings, meta, out1)
    render_weekly_html(listings, meta, out2)

    assert out1.read_bytes() == out2.read_bytes(), (
        "HTML renderer must produce byte-identical output for the same inputs"
    )


# ---------------------------------------------------------------------------
# Test 7: HTML document has expected structural elements
# ---------------------------------------------------------------------------

def test_html_document_structure(tmp_path: Path) -> None:
    listing = _make_listing()
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    assert "<!DOCTYPE html>" in content
    assert "<html" in content
    assert "<head>" in content
    assert "<body>" in content
    assert "<style>" in content, "CSS must be embedded in <style>"
    assert "<footer>" in content
    assert 'class="report-wrap"' in content


# ---------------------------------------------------------------------------
# Test 8: urgent listings appear in the urgent section
# ---------------------------------------------------------------------------

def test_urgent_listing_in_urgent_section(tmp_path: Path) -> None:
    from datetime import timedelta
    urgent_url = "https://www.reed.co.uk/jobs/pm/urgent1"
    urgent = _make_listing(
        source_listing_id="urgent1",
        source_url=urgent_url,
        source_urls=[urgent_url],
        start_date=_TODAY + timedelta(days=5),  # 5 days away → urgent
        title="Urgent PM Role",
    )
    meta = _make_run_metadata([urgent])
    out = render_weekly_html([urgent], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    assert 'id="urgent-starts"' in content
    assert urgent_url in content


# ---------------------------------------------------------------------------
# Test 9: review-queue listings appear with sanity reasons
# ---------------------------------------------------------------------------

def test_review_queue_listing_has_reasons(tmp_path: Path) -> None:
    geo_flagged = _make_listing(
        source_listing_id="geo1",
        source_url="https://www.reed.co.uk/jobs/pm/geo1",
        source_urls=["https://www.reed.co.uk/jobs/pm/geo1"],
        sanity_flags=["country_unknown_assumed_non_uk"],
        title="PM Role (Non-UK)",
    )
    meta = _make_run_metadata([geo_flagged])
    out = render_weekly_html([geo_flagged], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    assert 'id="review-queue"' in content
    # The sanity flag text should appear.
    assert "country_unknown_assumed_non_uk" in content


# ---------------------------------------------------------------------------
# Test 10: rate-period display in HTML
# ---------------------------------------------------------------------------

def test_hourly_rate_displays_per_hr(tmp_path: Path) -> None:
    listing = _make_listing(
        day_rate_min=46.0,
        day_rate_max=None,
        rate_period="hour",
        ir35_status="inside",
    )
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")
    assert "£46/hr" in content, "Hourly rate must display with /hr suffix in HTML"


# ---------------------------------------------------------------------------
# Test 11: every card has a data-source attribute
# ---------------------------------------------------------------------------

def test_every_card_has_data_source_attribute(tmp_path: Path) -> None:
    """Every <article class="role-card"> must carry a data-source attribute."""
    listings = [
        _make_listing(source_listing_id="c1", source="reed",
                      source_url="https://www.reed.co.uk/jobs/pm/c1",
                      source_urls=["https://www.reed.co.uk/jobs/pm/c1"]),
        _make_listing(source_listing_id="c2", source="adzuna",
                      source_url="https://www.adzuna.co.uk/jobs/c2",
                      source_urls=["https://www.adzuna.co.uk/jobs/c2"]),
    ]
    meta = _make_run_metadata(listings)
    out = render_weekly_html(listings, meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    import re
    # Count role-card article tags and data-source attributes on them
    card_tags = re.findall(r'<article[^>]*class="[^"]*role-card[^"]*"[^>]*>', content)
    assert len(card_tags) >= 2, "Expected at least 2 role-card articles"
    for tag in card_tags:
        assert 'data-source="' in tag, f"Card missing data-source attribute: {tag}"


# ---------------------------------------------------------------------------
# Test 12: filter bar lists all unique sources from input
# ---------------------------------------------------------------------------

def test_filter_bar_lists_all_unique_sources(tmp_path: Path) -> None:
    """The filter bar must contain one button per unique source in the data."""
    listings = [
        _make_listing(source_listing_id="r1", source="reed",
                      source_url="https://www.reed.co.uk/jobs/pm/r1",
                      source_urls=["https://www.reed.co.uk/jobs/pm/r1"]),
        _make_listing(source_listing_id="a1", source="adzuna",
                      source_url="https://www.adzuna.co.uk/jobs/a1",
                      source_urls=["https://www.adzuna.co.uk/jobs/a1"]),
        _make_listing(source_listing_id="a2", source="adzuna",
                      source_url="https://www.adzuna.co.uk/jobs/a2",
                      source_urls=["https://www.adzuna.co.uk/jobs/a2"]),
        _make_listing(source_listing_id="e1", source="energy_jobline",
                      source_url="https://www.energyjobline.com/jobs/e1",
                      source_urls=["https://www.energyjobline.com/jobs/e1"]),
    ]
    meta = _make_run_metadata(listings)
    out = render_weekly_html(listings, meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    # All three unique sources must appear as filter buttons in the filter bar
    assert 'data-src="reed"' in content, "Reed filter button missing"
    assert 'data-src="adzuna"' in content, "Adzuna filter button missing"
    assert 'data-src="energy-jobline"' in content, "Energy Jobline filter button missing"
    # Adzuna count badge should show 2
    assert "Adzuna (2)" in content, "Adzuna count badge should show 2"


# ---------------------------------------------------------------------------
# Test 13: embedded script block with filter logic is present
# ---------------------------------------------------------------------------

def test_filter_script_block_present(tmp_path: Path) -> None:
    """The HTML must contain a <script> block with the applyFilter function."""
    listing = _make_listing()
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    assert "<script>" in content, "<script> block must be embedded in the HTML"
    assert "applyFilter" in content, "Filter JS must define/call applyFilter"
    assert "filter-bar" in content, "Filter JS must reference filter-bar element"
    assert "data-source" in content, "Filter JS must reference data-source attribute"


# ---------------------------------------------------------------------------
# Test 14: every card has a data-region attribute
# ---------------------------------------------------------------------------

def test_every_card_has_data_region_attribute(tmp_path: Path) -> None:
    """Every <article class="role-card"> must carry a data-region attribute."""
    listings = [
        _make_listing(source_listing_id="r1", source="reed",
                      source_url="https://www.reed.co.uk/jobs/pm/r1",
                      source_urls=["https://www.reed.co.uk/jobs/pm/r1"],
                      location_normalized="manchester"),
        _make_listing(source_listing_id="r2", source="reed",
                      source_url="https://www.reed.co.uk/jobs/pm/r2",
                      source_urls=["https://www.reed.co.uk/jobs/pm/r2"],
                      location_normalized="london"),
        _make_listing(source_listing_id="r3", source="adzuna",
                      source_url="https://www.adzuna.co.uk/jobs/r3",
                      source_urls=["https://www.adzuna.co.uk/jobs/r3"],
                      location_normalized="remote"),
    ]
    meta = _make_run_metadata(listings)
    out = render_weekly_html(listings, meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    import re
    card_tags = re.findall(r'<article[^>]*class="[^"]*role-card[^"]*"[^>]*>', content)
    assert len(card_tags) >= 3, "Expected at least 3 role-card articles"
    for tag in card_tags:
        assert 'data-region="' in tag, f"Card missing data-region attribute: {tag[:120]}"


# ---------------------------------------------------------------------------
# Test 15: region filter bar lists expected region buttons
# ---------------------------------------------------------------------------

def test_region_filter_bar_lists_expected_regions(tmp_path: Path) -> None:
    """Region filter bar must contain one button per unique region in the data."""
    listings = [
        _make_listing(source_listing_id="ln1", source="reed",
                      source_url="https://www.reed.co.uk/jobs/pm/ln1",
                      source_urls=["https://www.reed.co.uk/jobs/pm/ln1"],
                      location_normalized="london"),
        _make_listing(source_listing_id="ln2", source="reed",
                      source_url="https://www.reed.co.uk/jobs/pm/ln2",
                      source_urls=["https://www.reed.co.uk/jobs/pm/ln2"],
                      location_normalized="city of london"),
        _make_listing(source_listing_id="mn1", source="adzuna",
                      source_url="https://www.adzuna.co.uk/jobs/mn1",
                      source_urls=["https://www.adzuna.co.uk/jobs/mn1"],
                      location_normalized="manchester"),
        _make_listing(source_listing_id="rm1", source="reed",
                      source_url="https://www.reed.co.uk/jobs/pm/rm1",
                      source_urls=["https://www.reed.co.uk/jobs/pm/rm1"],
                      location_normalized="remote"),
    ]
    meta = _make_run_metadata(listings)
    out = render_weekly_html(listings, meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    assert 'id="region-filter-bar"' in content, "Region filter bar div must be present"
    assert 'data-rgn="london"' in content, "London region button missing"
    assert 'data-rgn="north"' in content, "North region button (Manchester) missing"
    assert 'data-rgn="remote"' in content, "Remote region button missing"
    assert "London (2)" in content, "London count badge should show 2"


# ---------------------------------------------------------------------------
# Test 16: region filter JS references data-region and data-rgn
# ---------------------------------------------------------------------------

def test_region_filter_js_present(tmp_path: Path) -> None:
    """The embedded script must contain region-filter logic."""
    listing = _make_listing()
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    assert "data-rgn" in content, "Filter JS must reference data-rgn attribute"
    assert "activeRgn" in content, "Filter JS must maintain activeRgn state"
    assert "region-filter-bar" in content, "Filter JS must reference region-filter-bar"


# ---------------------------------------------------------------------------
# Test 17: source × region filters are combinable (both data attributes present)
# ---------------------------------------------------------------------------

def test_card_has_both_source_and_region_attributes(tmp_path: Path) -> None:
    """Each card must have both data-source and data-region so JS can AND them."""
    listing = _make_listing(location_normalized="birmingham")
    meta = _make_run_metadata([listing])
    out = render_weekly_html([listing], meta, tmp_path / "report.html")
    content = out.read_text(encoding="utf-8")

    import re
    card_tags = re.findall(r'<article[^>]*class="[^"]*role-card[^"]*"[^>]*>', content)
    assert card_tags, "No role-card article found"
    for tag in card_tags:
        assert 'data-source="' in tag, "Card missing data-source"
        assert 'data-region="' in tag, "Card missing data-region"
        assert 'data-region="midlands"' in tag, (
            f"Birmingham should map to midlands, got: {tag}"
        )
