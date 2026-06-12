"""Async coordination for automatic egg pool cache refreshes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from config import EGG_AUTO_UPDATE, EGG_CACHE_MAX_AGE_DAYS
from database.cache_metadata import is_cache_stale
from database.egg_pool_db import count_egg_pool_rows
from egg_update import CACHE_NAME, run_egg_update


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EggUpdateResult:
    attempted: bool
    updated: bool
    count: int
    reason: str
    stats: dict[str, Any] = field(default_factory=dict)


class EggCacheManager:
    """Prevent overlapping automatic/manual egg pool updates."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def is_update_running(self) -> bool:
        return self._lock.locked()

    def is_stale(self) -> bool:
        return is_cache_stale(CACHE_NAME, EGG_CACHE_MAX_AGE_DAYS)

    def has_cached_data(self) -> bool:
        return count_egg_pool_rows() > 0

    async def refresh_if_stale(self, reason: str) -> EggUpdateResult:
        """Refresh only when auto-update is enabled and metadata is stale."""

        if not EGG_AUTO_UPDATE:
            logger.info("Egg pool %s check: automatic updates are disabled.", reason)
            return EggUpdateResult(False, False, 0, "auto-update-disabled")
        if not self.is_stale():
            logger.info("Egg pool %s check: cache is fresh.", reason)
            return EggUpdateResult(False, False, 0, "fresh")
        return await self.force_refresh(reason=reason, wait_for_lock=False)

    async def force_refresh(self, reason: str, wait_for_lock: bool = True) -> EggUpdateResult:
        """Run the blocking updater in a worker thread, optionally failing fast if busy."""

        if self._lock.locked() and not wait_for_lock:
            logger.info("Egg pool %s update skipped: another update is already running.", reason)
            return EggUpdateResult(False, False, 0, "already-running")

        async with self._lock:
            logger.info("Egg pool %s update started.", reason)
            try:
                count, stats = await asyncio.to_thread(run_egg_update, force=(reason == "manual"))
            except Exception:
                logger.exception("Egg pool %s update failed.", reason)
                return EggUpdateResult(True, False, 0, "failed")
            if count <= 0:
                logger.warning("Egg pool %s update returned zero rows; no metadata update applied. Stats: %s", reason, stats)
                return EggUpdateResult(True, False, 0, "zero-rows", stats)
            logger.info("Egg pool %s update finished successfully with %d row(s).", reason, count)
            return EggUpdateResult(True, True, count, "updated", stats)


egg_cache_manager = EggCacheManager()
