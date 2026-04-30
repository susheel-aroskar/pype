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


def test_client_registry_register_increments_ids() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.register("secret-a")
    b = cr.register("secret-b")
    assert a.client_id != b.client_id
    assert a.client_id < b.client_id


def test_client_registry_get_returns_entry() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.register("secret")
    fetched = cr.get(a.client_id)
    assert fetched is a


def test_client_registry_remove_returns_and_clears() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.register("secret")
    assert cr.remove(a.client_id) is a
    assert cr.get(a.client_id) is None
    assert cr.remove(a.client_id) is None


async def test_client_registry_bump_last_seen_updates() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.register("secret")
    before = a.last_seen
    await asyncio.sleep(0.001)
    cr.bump_last_seen(a.client_id)
    assert a.last_seen > before


def test_client_registry_bump_last_seen_missing_is_noop() -> None:
    cr = ClientRegistry(queue_max_size=2)
    cr.bump_last_seen(9999)  # must not raise


def test_client_registry_snapshot_keys_independent_of_dict() -> None:
    cr = ClientRegistry(queue_max_size=2)
    a = cr.register("a")
    keys = cr.snapshot_keys()
    cr.remove(a.client_id)  # mutate after snapshot
    assert a.client_id in keys  # snapshot is independent


def test_client_registry_register_produces_unique_ids() -> None:
    cr = ClientRegistry(queue_max_size=2)
    ids = {cr.register(f"s{i}").client_id for i in range(50)}
    assert len(ids) == 50
