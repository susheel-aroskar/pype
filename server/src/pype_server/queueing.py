import asyncio
from typing import TypeVar

from pype_server.exceptions import TimeoutOutOfRangeError

T = TypeVar("T")


def normalize_timeout(timeout_ms: int | None, max_timeout_ms: int) -> int:
    if timeout_ms is None:
        return max_timeout_ms
    if timeout_ms < 0 or timeout_ms > max_timeout_ms:
        raise TimeoutOutOfRangeError(
            f"timeout must be in [0, {max_timeout_ms}] ms; got {timeout_ms}"
        )
    return timeout_ms


async def queue_put(queue: asyncio.Queue[T], item: T, timeout_ms: int) -> bool:
    """Put item into queue. Returns True on success, False on timeout (queue full)."""
    if timeout_ms == 0:
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False
    try:
        await asyncio.wait_for(queue.put(item), timeout=timeout_ms / 1000)
        return True
    except TimeoutError:
        return False


async def queue_get(queue: asyncio.Queue[T], timeout_ms: int) -> T | None:
    """Get item from queue. Returns item on success, None on timeout (queue empty)."""
    if timeout_ms == 0:
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout_ms / 1000)
    except TimeoutError:
        return None
