"""Mech-PM-Finder reporter package.

Exports the public surface used by the orchestrator and CLI.
"""
from mechpm.reporter.models import RunMetadata
from mechpm.reporter.render import render_weekly

__all__ = ["render_weekly", "RunMetadata"]
