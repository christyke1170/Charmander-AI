"""Async coordination for automatic raid attacker cache refreshes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from config import RAID_ATTACKER_AUTO_UPDATE, RAID_ATTACKER_CACHE_MAX_AGE_DAYS
from database.cache_metadata import is_cache_stale
from database.raid_attackers_db import count_raid_attacker_rankings
from raid_attacker_update import CACHE_NAME, run_raid_attacker_update


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RaidAttackerUpdateResult:
    attempted: bool
    updated: bool
    count: int
    reason: str


class RaidAttackerCacheManager:
    """Prevent overlapping automatic/manual raid attacker updates."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def is_update_running(self) -> bool:
        return self._lock.locked()

    def is_stale(self) -> bool:
        return is_cache_stale(CACHE_NAME, RAID_ATTACKER_CACHE_MAX_AGE_DAYS)

    def has_cached_data(self) -> bool:
        return count_raid_attacker_rankings() > 0

    async def refresh_if_stale(self, reason: str) -> RaidAttackerUpdateResult:
        """Refresh only when auto-update is enabled and metadata is stale."""

        if not RAID_ATTACKER_AUTO_UPDATE:
            logger.info("Raid attacker %s check: automatic updates are disabled.", reason)
            return RaidAttackerUpdateResult(False, False, 0, "auto-update-disabled")
        if not self.is_stale():
            logger.info("Raid attacker %s check: cache is fresh.", reason)
            return RaidAttackerUpdateResult(False, False, 0, "fresh")
        return await self.force_refresh(reason=reason, wait_for_lock=False)

    async def force_refresh(self, reason: str, wait_for_lock: bool = True) -> RaidAttackerUpdateResult:
        """Run the blocking updater in a worker thread, optionally failing fast if busy."""

        if self._lock.locked() and not wait_for_lock:
            logger.info("Raid attacker %s update skipped: another update is already running.", reason)
            return RaidAttackerUpdateResult(False, False, 0, "already-running")

        async with self._lock:
            logger.info("Raid attacker %s update started.", reason)
            try:
                count, stats = await asyncio.to_thread(run_raid_attacker_update)
            except Exception:
                logger.exception("Raid attacker %s update failed.", reason)
                return RaidAttackerUpdateResult(True, False, 0, "failed")
            if count <= 0:
                logger.warning(
                    "Raid attacker %s update returned zero rows; no metadata update applied. Stats: %s",
                    reason,
                    stats,
                )
                return RaidAttackerUpdateResult(True, False, 0, "zero-rows")
            logger.info("Raid attacker %s update finished successfully with %d row(s).", reason, count)
            return RaidAttackerUpdateResult(True, True, count, "updated")


raid_attacker_cache_manager = RaidAttackerCacheManager()