"""RailwayPeople.com adapter stub (Next.js SSR / Jobiqo platform) — next sprint."""
from __future__ import annotations

import logging
from datetime import datetime

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.railwaypeople")


class RailwayPeopleAdapter(SourceAdapter):
    """Adapter for RailwayPeople.com.

    Strategy: GET HTML, parse ``<script id="__NEXT_DATA__">`` JSON blob.
    Crawl-delay: 10 s (robots.txt directive).
    Not yet implemented — returns [] with a warning.
    """

    name = "railwaypeople"

    def __init__(self, crawl_delay: int = 10, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        logger.warning("RailwayPeople adapter is not yet implemented — returning [].")
        return []
