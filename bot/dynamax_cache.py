"""Async coordination for automatic Dynamax attacker cache refreshes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from config import DYNAMAX_AUTO_UPDATE, DYNAMAX_CACHE_MAX_AGE_DAYS
from database.cache_metadata import is_cache_stale
from database.dynamax_attackers_db import count_dynamax_attackers
from dynamax_update import CACHE_NAME, run_dynamax_update


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DynamaxUpdateResult:
    attempted: bool
    updated: bool
    count: int
    reason: str
    stats: dict[str, Any] = field(default_factory=dict)


class DynamaxCacheManager:
    """Prevent overlapping automatic/manual Dynamax attacker updates."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def is_update_running(self) -> bool:
        return self._lock.locked()

    def is_stale(self) -> bool:
        return is_cache_stale(CACHE_NAME, DYNAMAX_CACHE_MAX_AGE_DAYS)

    def has_cached_data(self) -> bool:
        return count_dynamax_attackers() > 0

    async def refresh_if_stale(self, reason: str) -> DynamaxUpdateResult:
        if not DYNAMAX_AUTO_UPDATE:
            logger.info("Dynamax %s check: automatic updates are disabled.", reason)
            return DynamaxUpdateResult(False, False, 0, "auto-update-disabled")
        if not self.is_stale():
            logger.info("Dynamax %s check: cache is fresh.", reason)
            return DynamaxUpdateResult(False, False, 0, "fresh")
        return await self.force_refresh(reason=reason, wait_for_lock=False)

    async def force_refresh(self, reason: str, wait_for_lock: bool = True) -> DynamaxUpdateResult:
        if self._lock.locked() and not wait_for_lock:
            logger.info("Dynamax %s update skipped: another update is already running.", reason)
            return DynamaxUpdateResult(False, False, 0, "already-running")

        async with self._lock:
            logger.info("Dynamax %s update started.", reason)
            try:
                count, stats = await asyncio.to_thread(run_dynamax_update, force=(reason == "manual"))
            except Exception:
                logger.exception("Dynamax %s update failed.", reason)
                return DynamaxUpdateResult(True, False, 0, "failed")
            if count <= 0:
                logger.warning("Dynamax %s update returned zero rows; no metadata update applied. Stats: %s", reason, stats)
                return DynamaxUpdateResult(True, False, 0, "zero-rows", stats)
            logger.info("Dynamax %s update finished successfully with %d row(s).", reason, count)
            return DynamaxUpdateResult(True, True, count, "updated", stats)


dynamax_cache_manager = DynamaxCacheManager()
