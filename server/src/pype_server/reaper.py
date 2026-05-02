import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pype_server.registries import ClientRegistry

logger = logging.getLogger(__name__)


class ClientQueueReaper:
    """Periodically evicts client_registry entries whose last_seen exceeds the inactivity
    threshold.

    Bounded work per tick; resumes across ticks via a snapshot+cursor so a large
    registry never monopolizes the event loop. The per-tick batch size **scales with
    registry size**: it's `max(min_batch_size, ceil(registry_size / ticks_per_sweep))`,
    where `ticks_per_sweep = target_sweep_seconds / period_seconds`. This guarantees the
    reaper completes a full sweep within `target_sweep_seconds` regardless of registry
    size — small registries do at most `min_batch_size` per tick (cheap), large
    registries scale up so stale entries don't sit around for hours.
    """

    def __init__(
        self,
        registry: ClientRegistry,
        period_seconds: float,
        batch_size: int,
        inactivity_threshold_seconds: float,
        target_sweep_seconds: float,
    ) -> None:
        self._registry = registry
        self._period_seconds = period_seconds
        self._min_batch_size = batch_size
        self._threshold_seconds = inactivity_threshold_seconds
        self._target_sweep_seconds = target_sweep_seconds
        self._cursor: list[str] = []
        self._cursor_idx = 0

    def _effective_batch_size(self) -> int:
        """Per-tick batch size. Scales up linearly with registry size so a full sweep
        always completes within `target_sweep_seconds`, but never falls below
        `min_batch_size` (so small registries aren't artificially throttled to a few
        entries per tick when they could be swept in one go)."""
        ticks_per_sweep = max(1, math.floor(self._target_sweep_seconds / self._period_seconds))
        scale_target = math.ceil(len(self._registry) / ticks_per_sweep)
        return max(self._min_batch_size, scale_target)

    def _tick(self) -> int:
        """Examine up to `_effective_batch_size()` keys; evict those over threshold.
        Returns count evicted."""
        if self._cursor_idx >= len(self._cursor):
            self._cursor = self._registry.snapshot_keys()
            self._cursor_idx = 0
        if not self._cursor:
            return 0

        evicted = 0
        now = time.monotonic()
        batch_size = self._effective_batch_size()
        end = min(self._cursor_idx + batch_size, len(self._cursor))
        for i in range(self._cursor_idx, end):
            client_id = self._cursor[i]
            entry = self._registry.get(client_id)
            if entry is None:
                continue
            if now - entry.last_seen > self._threshold_seconds:
                self._registry.remove(client_id)
                evicted += 1
        self._cursor_idx = end
        return evicted

    async def run(self) -> None:
        try:
            while True:
                try:
                    evicted = self._tick()
                    if evicted:
                        logger.info("client_reaper evicted %d stale client queues", evicted)
                except Exception:  # pragma: no cover - logged so the loop survives
                    logger.exception("client_reaper tick failed; continuing")
                await asyncio.sleep(self._period_seconds)
        except asyncio.CancelledError:
            logger.info("client_reaper cancelled, shutting down")
            raise


@asynccontextmanager
async def run_reaper(reaper: ClientQueueReaper) -> AsyncIterator[None]:
    task = asyncio.create_task(reaper.run(), name="client-queue-reaper")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
