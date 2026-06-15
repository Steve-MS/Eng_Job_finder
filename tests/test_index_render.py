"""Tests for the index page generator (index_render module)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mechpm.reporter.index_render import generate_index, _discover_reports


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    """Create a reports directory with sample HTML report files."""
    d = tmp_path / "reports"
    d.mkdir()
    # Create sample report files
    (d / "2026-06-15.html").write_text("<html><body>Report 15</body></html>")
    (d / "2026-06-08.html").write_text("<html><body>Report 08</body></html>")
    (d / "2026-06-01.html").write_text("<html><body>Report 01</body></html>")
    # Non-report files that should be ignored
    (d / "2026-06-15.md").write_text("# Markdown report")
    (d / "smoke-test-2026-06-12.md").write_text("# Smoke test")
    return d


@pytest.fixture
def empty_reports_dir(tmp_path: Path) -> Path:
    """Create an empty reports directory."""
    d = tmp_path / "reports"
    d.mkdir()
    return d


class TestDiscoverReports:
    def test_finds_dated_html_files(self, reports_dir: Path) -> None:
        dates = _discover_reports(reports_dir)
        assert dates == ["2026-06-15", "2026-06-08", "2026-06-01"]

    def test_ignores_non_date_files(self, reports_dir: Path) -> None:
        # Add an index.html and latest.html — should not appear
        (reports_dir / "index.html").write_text("<html></html>")
        (reports_dir / "latest.html").write_text("<html></html>")
        dates = _discover_reports(reports_dir)
        assert "index" not in str(dates)
        assert "latest" not in str(dates)
        assert len(dates) == 3

    def test_returns_empty_for_no_reports(self, empty_reports_dir: Path) -> None:
        dates = _discover_reports(empty_reports_dir)
        assert dates == []

    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        dates = _discover_reports(tmp_path / "nonexistent")
        assert dates == []


class TestGenerateIndex:
    def test_creates_index_html(self, reports_dir: Path) -> None:
        result = generate_index(reports_dir)
        assert result == reports_dir / "index.html"
        assert result.exists()

    def test_creates_latest_html(self, reports_dir: Path) -> None:
        generate_index(reports_dir)
        latest = reports_dir / "latest.html"
        assert latest.exists()

    def test_index_contains_all_report_links(self, reports_dir: Path) -> None:
        generate_index(reports_dir)
        content = (reports_dir / "index.html").read_text(encoding="utf-8")
        assert "2026-06-15.html" in content
        assert "2026-06-08.html" in content
        assert "2026-06-01.html" in content

    def test_index_newest_first(self, reports_dir: Path) -> None:
        generate_index(reports_dir)
        content = (reports_dir / "index.html").read_text(encoding="utf-8")
        pos_15 = content.index("2026-06-15.html")
        pos_08 = content.index("2026-06-08.html")
        pos_01 = content.index("2026-06-01.html")
        assert pos_15 < pos_08 < pos_01

    def test_latest_callout_points_to_newest(self, reports_dir: Path) -> None:
        generate_index(reports_dir)
        content = (reports_dir / "index.html").read_text(encoding="utf-8")
        assert "Latest Report" in content
        # The latest callout link should reference 2026-06-15
        assert 'href="2026-06-15.html"' in content

    def test_latest_html_redirects_to_newest(self, reports_dir: Path) -> None:
        generate_index(reports_dir)
        content = (reports_dir / "latest.html").read_text(encoding="utf-8")
        assert "2026-06-15.html" in content
        assert "refresh" in content.lower()

    def test_is_valid_html(self, reports_dir: Path) -> None:
        generate_index(reports_dir)
        content = (reports_dir / "index.html").read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "<html" in content
        assert "</html>" in content

    def test_empty_directory_produces_valid_output(self, empty_reports_dir: Path) -> None:
        result = generate_index(empty_reports_dir)
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "No reports" in content
        # latest.html still created
        latest = empty_reports_dir / "latest.html"
        assert latest.exists()

    def test_latest_badge_on_first_item(self, reports_dir: Path) -> None:
        generate_index(reports_dir)
        content = (reports_dir / "index.html").read_text(encoding="utf-8")
        assert "[Latest]" in content
