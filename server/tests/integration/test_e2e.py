"""End-to-end client → pype → service → pype → client roundtrips."""

import asyncio

from httpx import AsyncClient

from tests.conftest import auth_header


async def test_full_request_response_roundtrip(client: AsyncClient) -> None:
    """The canonical happy-path flow that combines every endpoint."""
    cl = (await client.post("/auth/client", json={"name": "alice"})).json()
    svc = (await client.post("/auth/service", json={"service_name": "doubler"})).json()

    async def fake_service() -> None:
        # Service pulls the client's request and pushes a response back.
        r = await client.get(
            "/services/doubler?timeout=2000", headers=auth_header(str(svc["access_token"]))
        )
        assert r.status_code == 200
        n = int(r.content)
        client_id = r.headers["x-pype-client-id"]
        client_secret = r.headers["x-pype-client-secret"]
        request_id = r.headers["x-pype-request-id"]
        await client.post(
            f"/clients/{client_id}?client_secret={client_secret}&request_id={request_id}",
            headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
            content=str(n * 2).encode(),
        )

    async def caller() -> bytes:
        await client.post(
            "/services/doubler?request_id=q1",
            headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
            content=b"21",
        )
        r = await client.get(
            f"/clients/{cl['client_id']}?timeout=2000",
            headers=auth_header(str(cl["access_token"])),
        )
        assert r.status_code == 200
        assert r.headers["x-pype-request-id"] == "q1"
        return r.content

    _, body = await asyncio.gather(fake_service(), caller())
    assert body == b"42"


async def test_responses_can_arrive_out_of_order(client: AsyncClient) -> None:
    """The spec explicitly allows out-of-order responses; the client side must tolerate it."""
    cl = (await client.post("/auth/client", json={"name": "alice"})).json()
    svc = (await client.post("/auth/service", json={"service_name": "slow"})).json()

    # Caller submits two requests.
    await client.post(
        "/services/slow?request_id=first",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"1",
    )
    await client.post(
        "/services/slow?request_id=second",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"2",
    )

    # Service drains both, then responds in reverse order.
    r1 = await client.get(
        "/services/slow?timeout=1000", headers=auth_header(str(svc["access_token"]))
    )
    r2 = await client.get(
        "/services/slow?timeout=1000", headers=auth_header(str(svc["access_token"]))
    )
    assert r1.headers["x-pype-request-id"] == "first"
    assert r2.headers["x-pype-request-id"] == "second"

    await client.post(
        f"/clients/{cl['client_id']}?client_secret={cl['client_secret']}&request_id=second",
        headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
        content=b"two",
    )
    await client.post(
        f"/clients/{cl['client_id']}?client_secret={cl['client_secret']}&request_id=first",
        headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
        content=b"one",
    )

    g1 = await client.get(
        f"/clients/{cl['client_id']}?timeout=500", headers=auth_header(str(cl["access_token"]))
    )
    g2 = await client.get(
        f"/clients/{cl['client_id']}?timeout=500", headers=auth_header(str(cl["access_token"]))
    )
    assert g1.headers["x-pype-request-id"] == "second"
    assert g1.content == b"two"
    assert g2.headers["x-pype-request-id"] == "first"
    assert g2.content == b"one"
