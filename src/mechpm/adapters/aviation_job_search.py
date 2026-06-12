"""Aviation Job Search adapter stub — next sprint."""
from __future__ import annotations

import logging
from datetime import datetime

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.aviation_job_search")


class AviationJobSearchAdapter(SourceAdapter):
    """Adapter for aviationjobsearch.com.

    Strategy: HTML scrape of /en-GB/jobs?title=project+manager&contract_types=2.
    robots.txt: allows all public paths; /api/* disallowed — use HTML only.
    Crawl-delay: 3 s.
    Not yet implemented — returns [] with a warning.
    """

    name = "aviation_job_search"

    def __init__(self, crawl_delay: int = 3, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        logger.warning("Aviation Job Search adapter is not yet implemented — returning [].")
        return []
