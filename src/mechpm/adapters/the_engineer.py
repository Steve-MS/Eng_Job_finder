"""The Engineer Jobs adapter stub (Mark Allen Group / Cloudflare) — next sprint."""
from __future__ import annotations

import logging
from datetime import datetime

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.the_engineer")


class TheEngineerAdapter(SourceAdapter):
    """Adapter for jobs.theengineer.co.uk.

    Strategy: HTML scrape. Cloudflare-fronted; requires browser-like headers.
    robots.txt: User-agent: * Allow: / — crawling is permitted.
    Crawl-delay: 5 s.
    Not yet implemented — returns [] with a warning.
    """

    name = "the_engineer"

    def __init__(self, crawl_delay: int = 5, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        logger.warning("The Engineer Jobs adapter is not yet implemented — returning [].")
        return []
