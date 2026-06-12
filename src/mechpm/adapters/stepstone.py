"""Totaljobs / CWJobs adapter stub (StepStone platform) — next sprint."""
from __future__ import annotations

import logging
from datetime import datetime

from mechpm.adapters.base import RawListing, SourceAdapter

logger = logging.getLogger("mechpm.adapter.stepstone")


class StepstoneAdapter(SourceAdapter):
    """Shared adapter for Totaljobs and CWJobs (both StepStone platform).

    Not yet implemented — returns [] with a warning.
    """

    name = "stepstone"

    def __init__(self, crawl_delay: int = 3, **kwargs: object) -> None:
        self.crawl_delay = crawl_delay

    async def fetch(self, since: datetime | None = None) -> list[RawListing]:
        logger.warning("StepStone adapter is not yet implemented — returning [].")
        return []
