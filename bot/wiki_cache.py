"""Async coordination for automatic Pokémon GO Wiki knowledge cache refreshes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from config import WIKI_AUTO_UPDATE, WIKI_CACHE_MAX_AGE_DAYS
from database.cache_metadata import is_cache_stale
from database.wiki_knowledge_db import count_wiki_chunks
from wiki_update import CACHE_NAME, run_wiki_update


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WikiUpdateResult:
    attempted: bool
    updated: bool
    count: int
    reason: str
    stats: dict[str, Any] = field(default_factory=dict)


class WikiCacheManager:
    """Prevent overlapping automatic/manual wiki knowledge updates."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def is_update_running(self) -> bool:
        return self._lock.locked()

    def is_stale(self) -> bool:
        return is_cache_stale(CACHE_NAME, WIKI_CACHE_MAX_AGE_DAYS)

    def has_cached_data(self) -> bool:
        return count_wiki_chunks() > 0

    async def refresh_if_stale(self, reason: str) -> WikiUpdateResult:
        """Refresh only when auto-update is enabled and metadata is stale."""

        if not WIKI_AUTO_UPDATE:
            logger.info("Wiki %s check: automatic updates are disabled.", reason)
            return WikiUpdateResult(False, False, 0, "auto-update-disabled")
        if not self.is_stale():
            logger.info("Wiki %s check: cache is fresh.", reason)
            return WikiUpdateResult(False, False, 0, "fresh")
        return await self.force_refresh(reason=reason, wait_for_lock=False)

    async def force_refresh(self, reason: str, wait_for_lock: bool = True) -> WikiUpdateResult:
        """Run the blocking updater in a worker thread, optionally failing fast if busy."""

        if self._lock.locked() and not wait_for_lock:
            logger.info("Wiki %s update skipped: another update is already running.", reason)
            return WikiUpdateResult(False, False, 0, "already-running")

        async with self._lock:
            logger.info("Wiki %s update started.", reason)
            try:
                count, stats = await asyncio.to_thread(run_wiki_update)
            except Exception:
                logger.exception("Wiki %s update failed.", reason)
                return WikiUpdateResult(True, False, 0, "failed")
            if count <= 0:
                logger.warning("Wiki %s update returned zero chunks; no metadata update applied. Stats: %s", reason, stats)
                return WikiUpdateResult(True, False, 0, "zero-rows", stats)
            logger.info("Wiki %s update finished successfully with %d chunk(s).", reason, count)
            return WikiUpdateResult(True, True, count, "updated", stats)


wiki_cache_manager = WikiCacheManager()
