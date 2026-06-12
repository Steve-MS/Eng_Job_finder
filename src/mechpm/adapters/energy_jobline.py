"""Energy Jobline adapter stub (Jobiqo/Drupal platform) — next sprint."""
from __future__ import annotations

import logging
from datetime import datetime

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.energy_jobline")


class EnergyJoblineAdapter(SourceAdapter):
    """Adapter for energyjobline.com.

    Strategy: HTML scrape of /jobs?keywords=...&contract_type=contract.
    Crawl-delay: 10 s (robots.txt directive).
    Not yet implemented — returns [] with a warning.
    """

    name = "energy_jobline"

    def __init__(self, crawl_delay: int = 10, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        logger.warning("Energy Jobline adapter is not yet implemented — returning [].")
        return []
