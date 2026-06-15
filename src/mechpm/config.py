"""Load .env + config.toml and expose a typed Settings model."""
from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("mechpm.config")

_DEFAULT_KEYWORDS = "project manager mechanical engineering"


class SourceConfig(BaseModel):
    """Per-source configuration block from config.toml [[sources.*]]."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    crawl_delay: int = 0
    keywords: str = _DEFAULT_KEYWORDS
    keywords_list: list[str] = Field(default_factory=list)
    location: str = "UK"
    results_to_take: int = 100
    safety_cap: int = 500

    @model_validator(mode="after")
    def _normalise_keywords(self) -> "SourceConfig":
        """Ensure ``keywords_list`` is always populated.

        Resolution order (highest priority first):
        1. ``keywords_list`` (TOML array) — use as-is when non-empty.
           If ``keywords`` is also non-default, emit a deprecation warning.
        2. ``keywords`` (scalar string, legacy) — wrap into a single-item list.
        """
        if self.keywords_list:
            if self.keywords != _DEFAULT_KEYWORDS:
                logger.warning(
                    "Both 'keywords' and 'keywords_list' are set in source config — "
                    "'keywords' is deprecated when 'keywords_list' is present. "
                    "Remove 'keywords' from config.toml."
                )
        else:
            self.keywords_list = [self.keywords]
        return self


class Settings(BaseModel):
    """Top-level application settings assembled from .env and config.toml."""

    reed_api_key: str = ""
    openai_api_key: str | None = None
    sources: dict[str, SourceConfig] = Field(default_factory=dict)

    @classmethod
    def load(
        cls,
        config_path: Path | None = None,
        env_path: Path | None = None,
    ) -> "Settings":
        """Load settings from .env (via python-dotenv) and config.toml (stdlib tomllib)."""
        load_dotenv(dotenv_path=env_path)

        if config_path is None:
            # Prefer config.toml in cwd; fall back to repo root relative to this file.
            cwd_config = Path("config.toml")
            repo_root = Path(__file__).parent.parent.parent
            config_path = cwd_config if cwd_config.exists() else repo_root / "config.toml"

        toml_data: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path, "rb") as fh:
                toml_data = tomllib.load(fh)
        else:
            logger.warning("config.toml not found at %s — using defaults.", config_path)

        sources = {
            name: SourceConfig(**cfg)
            for name, cfg in toml_data.get("sources", {}).items()
        }

        return cls(
            reed_api_key=os.environ.get("REED_API_KEY", ""),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            sources=sources,
        )

    def enabled_sources(self) -> list[str]:
        """Return names of sources with enabled = true."""
        return [name for name, cfg in self.sources.items() if cfg.enabled]
