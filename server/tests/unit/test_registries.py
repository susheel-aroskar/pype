import asyncio

from pype_server.registries import ClientRegistry, ServiceRegistry


def test_service_registry_creates_queue_on_first_access() -> None:
    r = ServiceRegistry(queue_max_size=8)
    q = r.get_or_create("svc")
    assert isinstance(q, asyncio.Queue)
    assert q.maxsize == 8
    assert r.get_or_create("svc") is q  # idempotent


def test_service_registry_snapshot_loads_reflects_qsize() -> None:
    r = ServiceRegistry(queue_max_size=8)
    q = r.get_or_create("svc")
    assert ("svc", 0) in r.snapshot_loads()
    q.put_nowait("x")  # type: ignore[arg-type]
    q.put_nowait("y")  # type: ignore[arg-type]
    assert ("svc", 2) in r.snapshot_loads()


def test_client_registry_get_or_create_keys_by_caller_supplied_id() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.get_or_create("client-id-a")
    b = cr.get_or_create("client-id-b")
    assert a.client_id == "client-id-a"
    assert b.client_id == "client-id-b"
    assert cr.get("client-id-a") is a
    assert cr.get("client-id-b") is b


def test_client_registry_get_or_create_is_idempotent() -> None:
    """Two calls with the same id return the same entry — that's the lazy-create
    invariant the routers rely on."""
    cr = ClientRegistry(queue_max_size=2)
    first = cr.get_or_create("cid")
    second = cr.get_or_create("cid")
    assert first is second


def test_client_registry_get_returns_entry() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.get_or_create("cid")
    fetched = cr.get("cid")
    assert fetched is a


def test_client_registry_remove_returns_and_clears() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.get_or_create("cid")
    assert cr.remove("cid") is a
    assert cr.get("cid") is None
    assert cr.remove("cid") is None


async def test_client_registry_bump_last_seen_updates() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.get_or_create("cid")
    before = a.last_seen
    await asyncio.sleep(0.001)
    cr.bump_last_seen("cid")
    assert a.last_seen > before


def test_client_registry_bump_last_seen_missing_is_noop() -> None:
    cr = ClientRegistry(queue_max_size=2)
    cr.bump_last_seen("nonexistent-id")  # must not raise


def test_client_registry_snapshot_keys_independent_of_dict() -> None:
    cr = ClientRegistry(queue_max_size=2)
    cr.get_or_create("cid-a")
    keys = cr.snapshot_keys()
    cr.remove("cid-a")  # mutate after snapshot
    assert "cid-a" in keys  # snapshot is independent
