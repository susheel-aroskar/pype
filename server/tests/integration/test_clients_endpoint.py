import asyncio

from httpx import AsyncClient

from tests.conftest import auth_header


async def _auth_client(c: AsyncClient, name: str = "alice") -> dict[str, object]:
    return (await c.post("/auth/client", json={"name": name})).json()


async def _auth_service(c: AsyncClient, service_name: str) -> dict[str, object]:
    return (await c.post("/auth/service", json={"service_name": service_name})).json()


async def test_post_response_to_client_returns_202(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "echo")
    r = await client.post(
        f"/clients/{cl['client_id']}?request_id=r1",
        headers={**auth_header(str(svc["access_token"])), "Content-Type": "application/json"},
        content=b'{"ok": true}',
    )
    assert r.status_code == 202


async def test_post_lazy_creates_client_entry_on_unknown_id(client: AsyncClient) -> None:
    """A service POSTing to an unknown client_id does NOT 410 anymore — the entry is
    lazy-created. This is what lets a client's JWT seamlessly bounce across pype
    instances behind a load balancer: any instance can spin up an entry on demand."""
    svc = await _auth_service(client, "echo")
    r = await client.post(
        "/clients/lazy-create-target-id?request_id=r1",
        headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
        content=b"hello",
    )
    assert r.status_code == 202


async def test_post_requires_service_jwt(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    r = await client.post(
        f"/clients/{cl['client_id']}?request_id=r1",
        headers=auth_header(str(cl["access_token"])),  # client token, not service
        content=b"",
    )
    assert r.status_code == 400


async def test_get_returns_204_when_empty(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    r = await client.get(
        f"/clients/{cl['client_id']}?timeout=0",
        headers=auth_header(str(cl["access_token"])),
    )
    assert r.status_code == 204


async def test_get_path_must_match_jwt_client_id(client: AsyncClient) -> None:
    a = await _auth_client(client, "alice")
    b = await _auth_client(client, "bob")
    r = await client.get(
        f"/clients/{b['client_id']}?timeout=0",
        headers=auth_header(str(a["access_token"])),
    )
    assert r.status_code == 403


async def test_get_lazy_creates_queue_after_logoff(client: AsyncClient) -> None:
    """In the lazy-create model, a GET after DELETE /auth/client just creates a fresh
    empty queue and 204s on timeout. The client's JWT is the sole authority — entry
    presence is bookkeeping, not auth."""
    cl = await _auth_client(client)
    await client.delete("/auth/client", headers=auth_header(str(cl["access_token"])))
    r = await client.get(
        f"/clients/{cl['client_id']}?timeout=0",
        headers=auth_header(str(cl["access_token"])),
    )
    assert r.status_code == 204


async def test_post_get_roundtrip_preserves_request_id_and_content_type(
    client: AsyncClient,
) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "echo")
    payload = b"binary\x00\xff"
    await client.post(
        f"/clients/{cl['client_id']}?request_id=req-7",
        headers={
            **auth_header(str(svc["access_token"])),
            "Content-Type": "application/octet-stream",
        },
        content=payload,
    )
    r = await client.get(
        f"/clients/{cl['client_id']}?timeout=1000",
        headers=auth_header(str(cl["access_token"])),
    )
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["x-pype-request-id"] == "req-7"
    assert r.headers["content-type"] == "application/octet-stream"


async def test_get_blocks_until_response_arrives(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "echo")

    async def post_after_delay() -> None:
        await asyncio.sleep(0.05)
        await client.post(
            f"/clients/{cl['client_id']}?request_id=late",
            headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
            content=b"hi",
        )

    get_task = asyncio.create_task(
        client.get(
            f"/clients/{cl['client_id']}?timeout=2000",
            headers=auth_header(str(cl["access_token"])),
        )
    )
    await asyncio.gather(post_after_delay(), asyncio.sleep(0))
    r = await get_task
    assert r.status_code == 200
    assert r.content == b"hi"
    assert r.headers["x-pype-request-id"] == "late"


async def test_responses_are_returned_in_fifo_order_per_client_queue(
    client: AsyncClient,
) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "echo")
    for i, body in enumerate([b"a", b"b", b"c"]):
        r = await client.post(
            f"/clients/{cl['client_id']}?request_id=r{i}",
            headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
            content=body,
        )
        assert r.status_code == 202
    seen: list[tuple[str, bytes]] = []
    for _ in range(3):
        r = await client.get(
            f"/clients/{cl['client_id']}?timeout=500",
            headers=auth_header(str(cl["access_token"])),
        )
        assert r.status_code == 200
        seen.append((r.headers["x-pype-request-id"], r.content))
    assert seen == [("r0", b"a"), ("r1", b"b"), ("r2", b"c")]


async def test_get_bumps_last_seen(app: object, client: AsyncClient) -> None:
    cl = await _auth_client(client)
    # In the lazy-create model, no entry exists immediately after auth. A GET creates
    # one; we use the first GET to bring the entry into being and capture its initial
    # last_seen, then assert that a second GET advances it.
    r1 = await client.get(
        f"/clients/{cl['client_id']}?timeout=0",
        headers=auth_header(str(cl["access_token"])),
    )
    assert r1.status_code == 204
    registry = app.state.client_registry  # type: ignore[attr-defined]
    entry = registry.get(cl["client_id"])
    assert entry is not None
    before = entry.last_seen
    await asyncio.sleep(0.02)
    r2 = await client.get(
        f"/clients/{cl['client_id']}?timeout=0",
        headers=auth_header(str(cl["access_token"])),
    )
    assert r2.status_code == 204
    assert entry.last_seen > before


async def test_post_does_not_bump_last_seen(app: object, client: AsyncClient) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "echo")
    # Materialize the entry first via a GET so we can capture last_seen, then verify
    # that a service-side POST /clients/{id} (which does NOT count as the client itself
    # being active) does not advance it.
    await client.get(
        f"/clients/{cl['client_id']}?timeout=0",
        headers=auth_header(str(cl["access_token"])),
    )
    registry = app.state.client_registry  # type: ignore[attr-defined]
    entry = registry.get(cl["client_id"])
    assert entry is not None
    before = entry.last_seen
    await asyncio.sleep(0.02)
    r = await client.post(
        f"/clients/{cl['client_id']}?request_id=r1",
        headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert r.status_code == 202
    assert entry.last_seen == before
