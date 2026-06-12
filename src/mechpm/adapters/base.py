"""Base adapter contract: RawListing Pydantic model and SourceAdapter ABC."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class RawListing(BaseModel):
    """Canonical raw-listing record produced by every adapter.

    Fields outside this shape should be placed in ``metadata``.
    """

    source: str
    source_listing_id: str
    url: str
    title: str
    employer: str | None = None
    agency: str | None = None
    location_raw: str | None = None
    description_raw: str | None = None
    posted_at: datetime | None = None
    salary_raw: str | None = None
    contract_type_raw: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class SourceAdapter(ABC):
    """Abstract base class for all source adapters.

    Implementors MUST:
    - Set ``name`` (class attribute) and ``crawl_delay`` (instance attribute).
    - Catch ALL exceptions inside ``fetch()`` and return ``[]`` on failure,
      logging a warning via the Python ``logging`` module.
    - Never call ``print()`` except in ``__main__`` self-test blocks.
    """

    name: str
    crawl_delay: int  # seconds; orchestrator sleeps this long between adapters

    @abstractmethod
    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        """Fetch listings from the source.

        Args:
            since: If set, return only listings posted at or after this datetime.

        Returns:
            A list of ``RawListing`` objects; always an empty list on failure.
        """
        ...
