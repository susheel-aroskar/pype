import time

import pytest
from pype_server.reaper import ClientQueueReaper
from pype_server.registries import ClientRegistry


@pytest.fixture
def registry() -> ClientRegistry:
    return ClientRegistry(queue_max_size=4)


def _new_reaper(
    registry: ClientRegistry, threshold: float, batch_size: int = 1000
) -> ClientQueueReaper:
    return ClientQueueReaper(
        registry=registry,
        period_seconds=0.01,  # never relied on in unit tests; we call _tick directly
        batch_size=batch_size,
        inactivity_threshold_seconds=threshold,
    )


def test_reaper_evicts_stale_entries(registry: ClientRegistry) -> None:
    a = registry.register("a")
    b = registry.register("b")
    a.last_seen = time.monotonic() - 100  # stale
    # b is fresh
    reaper = _new_reaper(registry, threshold=10)
    evicted = reaper._tick()
    assert evicted == 1
    assert registry.get(a.client_id) is None
    assert registry.get(b.client_id) is b


def test_reaper_keeps_fresh_entries(registry: ClientRegistry) -> None:
    a = registry.register("a")
    reaper = _new_reaper(registry, threshold=100)
    assert reaper._tick() == 0
    assert registry.get(a.client_id) is a


def test_reaper_resumes_across_ticks_with_small_batch(registry: ClientRegistry) -> None:
    for i in range(5):
        e = registry.register(f"s{i}")
        e.last_seen = time.monotonic() - 100  # all stale
    reaper = _new_reaper(registry, threshold=10, batch_size=2)
    evicted_per_tick = [reaper._tick() for _ in range(3)]
    assert evicted_per_tick == [2, 2, 1]
    assert len(registry) == 0


def test_reaper_handles_concurrent_removal_silently(registry: ClientRegistry) -> None:
    a = registry.register("a")
    b = registry.register("b")
    a.last_seen = time.monotonic() - 100
    b.last_seen = time.monotonic() - 100
    reaper = _new_reaper(registry, threshold=10, batch_size=10)
    # Force snapshot to be taken first.
    reaper._cursor = registry.snapshot_keys()
    reaper._cursor_idx = 0
    # Now remove `a` out from under the reaper.
    registry.remove(a.client_id)
    evicted = reaper._tick()
    assert evicted == 1  # only b should be evicted; a's gone-key path is silent
    assert registry.get(b.client_id) is None


def test_reaper_takes_fresh_snapshot_when_cursor_exhausted(registry: ClientRegistry) -> None:
    a = registry.register("a")
    a.last_seen = time.monotonic() - 100
    reaper = _new_reaper(registry, threshold=10)
    assert reaper._tick() == 1
    assert reaper._cursor_idx == 1
    # Now add a new stale entry; the cursor was exhausted, so the next tick should
    # re-snapshot and pick it up.
    b = registry.register("b")
    b.last_seen = time.monotonic() - 100
    assert reaper._tick() == 1
    assert registry.get(b.client_id) is None


def test_reaper_no_op_on_empty_registry(registry: ClientRegistry) -> None:
    reaper = _new_reaper(registry, threshold=10)
    assert reaper._tick() == 0
