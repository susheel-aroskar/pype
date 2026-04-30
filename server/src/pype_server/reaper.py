import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pype_server.registries import ClientRegistry

logger = logging.getLogger(__name__)


class ClientQueueReaper:
    """Periodically evicts client_registry entries whose last_seen exceeds the inactivity threshold.

    Bounded work per tick (batch_size keys); resumes across ticks via a snapshot+cursor so a
    long registry never monopolizes the event loop and a fresh snapshot is taken only when the
    cursor is exhausted. Snapshot is immune to concurrent dict mutation.
    """

    def __init__(
        self,
        registry: ClientRegistry,
        period_seconds: float,
        batch_size: int,
        inactivity_threshold_seconds: float,
    ) -> None:
        self._registry = registry
        self._period_seconds = period_seconds
        self._batch_size = batch_size
        self._threshold_seconds = inactivity_threshold_seconds
        self._cursor: list[int] = []
        self._cursor_idx = 0

    def _tick(self) -> int:
        """Examine up to batch_size keys; evict those over threshold. Returns count evicted."""
        if self._cursor_idx >= len(self._cursor):
            self._cursor = self._registry.snapshot_keys()
            self._cursor_idx = 0
        if not self._cursor:
            return 0

        evicted = 0
        now = time.monotonic()
        end = min(self._cursor_idx + self._batch_size, len(self._cursor))
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
