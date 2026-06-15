"""Unit tests for config.py — keywords_list normalisation (M7).

Verifies:
1. When only ``keywords`` (scalar) is set, ``keywords_list`` is populated
   as a single-item list wrapping the scalar.
2. When ``keywords_list`` (array) is explicitly provided, it is used as-is.
3. When both are present, ``keywords_list`` wins and a deprecation warning
   is logged.
4. When ``keywords_list`` is provided with the default ``keywords``, no
   deprecation warning is emitted.
"""
from __future__ import annotations

import logging

import pytest

from mechpm.config import SourceConfig


# ---------------------------------------------------------------------------
# Scalar → list wrapping
# ---------------------------------------------------------------------------

class TestKeywordsNormalisation:
    def test_scalar_wrapped_into_list(self):
        """Single 'keywords' scalar must be wrapped into a one-item keywords_list."""
        cfg = SourceConfig(keywords="project manager HVAC")
        assert cfg.keywords_list == ["project manager HVAC"], (
            f"Expected ['project manager HVAC'], got {cfg.keywords_list!r}"
        )

    def test_default_keywords_wrapped(self):
        """Default keywords scalar must also be wrapped when no keywords_list given."""
        cfg = SourceConfig()
        assert len(cfg.keywords_list) == 1
        assert cfg.keywords_list[0] == cfg.keywords

    # ---------------------------------------------------------------------------
    # Explicit keywords_list
    # ---------------------------------------------------------------------------

    def test_explicit_keywords_list_used_as_is(self):
        """When keywords_list is explicitly provided, it must be stored unchanged."""
        kw_list = ["project manager mechanical", "project manager HVAC", "eng PM"]
        cfg = SourceConfig(keywords_list=kw_list)
        assert cfg.keywords_list == kw_list

    def test_explicit_keywords_list_single_item(self):
        """Single-item keywords_list is valid."""
        cfg = SourceConfig(keywords_list=["project manager M&E"])
        assert cfg.keywords_list == ["project manager M&E"]

    # ---------------------------------------------------------------------------
    # Both present → deprecation warning
    # ---------------------------------------------------------------------------

    def test_both_present_prefers_list_and_warns(self, caplog):
        """When both keywords and keywords_list are set, list wins and a WARNING is logged."""
        with caplog.at_level(logging.WARNING, logger="mechpm.config"):
            cfg = SourceConfig(
                keywords="old scalar keyword",
                keywords_list=["new", "list", "keywords"],
            )
        assert cfg.keywords_list == ["new", "list", "keywords"], (
            "keywords_list should take precedence over keywords scalar"
        )
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("deprecated" in msg.lower() for msg in warning_messages), (
            f"Expected a deprecation warning, got: {warning_messages}"
        )

    def test_default_keywords_with_list_no_warning(self, caplog):
        """Providing keywords_list alongside the *default* keywords emits no warning."""
        with caplog.at_level(logging.WARNING, logger="mechpm.config"):
            cfg = SourceConfig(keywords_list=["a", "b"])
        assert cfg.keywords_list == ["a", "b"]
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("deprecated" in msg.lower() for msg in warning_messages), (
            f"Unexpected deprecation warning: {warning_messages}"
        )
