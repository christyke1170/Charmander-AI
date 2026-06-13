"""Async coordination for automatic PvPoke ranking cache refreshes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from config import PVP_AUTO_UPDATE, PVP_CACHE_MAX_AGE_DAYS
from database.cache_metadata import is_cache_stale
from database.pvp_rankings_db import count_pvp_rankings
from pvp_update import CACHE_NAME, run_pvp_update


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PvpUpdateResult:
    attempted: bool
    updated: bool
    count: int
    reason: str
    stats: dict[str, Any] = field(default_factory=dict)


class PvpCacheManager:
    """Prevent overlapping automatic/manual PvPoke ranking updates."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def is_update_running(self) -> bool:
        return self._lock.locked()

    def is_stale(self) -> bool:
        return is_cache_stale(CACHE_NAME, PVP_CACHE_MAX_AGE_DAYS)

    def has_cached_data(self) -> bool:
        return count_pvp_rankings() > 0

    async def refresh_if_stale(self, reason: str) -> PvpUpdateResult:
        """Refresh only when auto-update is enabled and metadata is stale."""

        if not PVP_AUTO_UPDATE:
            logger.info("PvP %s check: automatic updates are disabled.", reason)
            return PvpUpdateResult(False, False, 0, "auto-update-disabled")
        if not self.is_stale():
            logger.info("PvP %s check: cache is fresh.", reason)
            return PvpUpdateResult(False, False, 0, "fresh")
        return await self.force_refresh(reason=reason, wait_for_lock=False)

    async def force_refresh(self, reason: str, wait_for_lock: bool = True) -> PvpUpdateResult:
        """Run the blocking updater in a worker thread, optionally failing fast if busy."""

        if self._lock.locked() and not wait_for_lock:
            logger.info("PvP %s update skipped: another update is already running.", reason)
            return PvpUpdateResult(False, False, 0, "already-running")

        async with self._lock:
            logger.info("PvP %s update started.", reason)
            try:
                count, stats = await asyncio.to_thread(run_pvp_update)
            except Exception:
                logger.exception("PvP %s update failed.", reason)
                return PvpUpdateResult(True, False, 0, "failed")
            if count <= 0:
                logger.warning("PvP %s update returned zero rows; no metadata update applied. Stats: %s", reason, stats)
                return PvpUpdateResult(True, False, 0, "zero-rows", stats)
            logger.info("PvP %s update finished successfully with %d row(s).", reason, count)
            return PvpUpdateResult(True, True, count, "updated", stats)


pvp_cache_manager = PvpCacheManager()