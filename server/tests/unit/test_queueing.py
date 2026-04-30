import asyncio

import pytest
from pype_server.exceptions import TimeoutOutOfRangeError
from pype_server.queueing import normalize_timeout, queue_get, queue_put


def test_normalize_timeout_none_uses_max() -> None:
    assert normalize_timeout(None, 5000) == 5000


@pytest.mark.parametrize("v", [0, 1, 4999, 5000])
def test_normalize_timeout_in_range(v: int) -> None:
    assert normalize_timeout(v, 5000) == v


@pytest.mark.parametrize("v", [-1, 5001, 1_000_000])
def test_normalize_timeout_out_of_range_raises(v: int) -> None:
    with pytest.raises(TimeoutOutOfRangeError):
        normalize_timeout(v, 5000)


async def test_queue_put_zero_timeout_succeeds_when_space() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=2)
    assert await queue_put(q, 1, timeout_ms=0) is True
    assert q.qsize() == 1


async def test_queue_put_zero_timeout_returns_false_when_full() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    await queue_put(q, 1, timeout_ms=0)
    assert await queue_put(q, 2, timeout_ms=0) is False


async def test_queue_get_zero_timeout_returns_none_when_empty() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    assert await queue_get(q, timeout_ms=0) is None


async def test_queue_get_zero_timeout_returns_value() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    q.put_nowait(5)
    assert await queue_get(q, timeout_ms=0) == 5


async def test_queue_put_blocks_until_space_available() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    q.put_nowait(1)

    async def drain_after_delay() -> None:
        await asyncio.sleep(0.02)
        await q.get()

    drainer = asyncio.create_task(drain_after_delay())
    assert await queue_put(q, 2, timeout_ms=500) is True
    await drainer


async def test_queue_put_returns_false_after_timeout_elapses() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    q.put_nowait(1)
    assert await queue_put(q, 2, timeout_ms=20) is False


async def test_queue_get_returns_none_after_timeout_elapses() -> None:
    q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    assert await queue_get(q, timeout_ms=20) is None
