import asyncio

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header


async def _auth_client(c: AsyncClient, name: str = "alice") -> dict[str, object]:
    return (await c.post("/auth/client", json={"name": name})).json()


async def _auth_service(c: AsyncClient, service_name: str) -> dict[str, object]:
    return (await c.post("/auth/service", json={"service_name": service_name})).json()


async def test_post_request_to_service_returns_202(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    r = await client.post(
        "/services/billing?request_id=r1",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "application/json"},
        content=b'{"amount": 5}',
    )
    assert r.status_code == 202


async def test_post_requires_client_jwt(client: AsyncClient) -> None:
    svc = await _auth_service(client, "billing")
    r = await client.post(
        "/services/billing?request_id=r1",
        headers=auth_header(str(svc["access_token"])),
        content=b"x",
    )
    # Service token has wrong role for POST /services/{n}.
    assert r.status_code == 400


async def test_get_for_service_returns_204_when_empty(client: AsyncClient) -> None:
    svc = await _auth_service(client, "billing")
    r = await client.get(
        "/services/billing?timeout=0", headers=auth_header(str(svc["access_token"]))
    )
    assert r.status_code == 204


async def test_get_service_name_must_match_token(client: AsyncClient) -> None:
    svc = await _auth_service(client, "billing")
    r = await client.get(
        "/services/shipping?timeout=0", headers=auth_header(str(svc["access_token"]))
    )
    assert r.status_code == 400


async def test_post_then_get_roundtrips_payload_and_headers(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "billing")
    payload = b'{"amount": 7}'
    await client.post(
        "/services/billing?request_id=req-42",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "application/json"},
        content=payload,
    )
    r = await client.get(
        "/services/billing?timeout=1000", headers=auth_header(str(svc["access_token"]))
    )
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["x-pype-client-id"] == cl["client_id"]
    assert "x-pype-client-secret" not in r.headers
    assert r.headers["x-pype-request-id"] == "req-42"


async def test_binary_payload_preserved_with_custom_content_type(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "binary")
    payload = bytes(range(256))
    await client.post(
        "/services/binary?request_id=b1",
        headers={
            **auth_header(str(cl["access_token"])),
            "Content-Type": "application/octet-stream",
        },
        content=payload,
    )
    r = await client.get(
        "/services/binary?timeout=1000", headers=auth_header(str(svc["access_token"]))
    )
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-type"] == "application/octet-stream"


async def test_get_blocks_until_post(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "later")

    async def post_after_delay() -> None:
        await asyncio.sleep(0.05)
        await client.post(
            "/services/later?request_id=delayed",
            headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
            content=b"hello",
        )

    get_task = asyncio.create_task(
        client.get("/services/later?timeout=2000", headers=auth_header(str(svc["access_token"])))
    )
    await asyncio.gather(post_after_delay(), asyncio.sleep(0))
    r = await get_task
    assert r.status_code == 200
    assert r.content == b"hello"


async def test_post_returns_503_when_queue_full_with_zero_timeout(client: AsyncClient) -> None:
    # service_queue_max_size == 4 in test settings.
    cl = await _auth_client(client)
    for i in range(4):
        r = await client.post(
            f"/services/full?request_id=r{i}&timeout=0",
            headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
            content=b"x",
        )
        assert r.status_code == 202
    overflow = await client.post(
        "/services/full?request_id=overflow&timeout=0",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert overflow.status_code == 503


@pytest.mark.parametrize("bad_timeout", [-1, 6_000_000, 5_001])
async def test_post_rejects_out_of_range_timeout(client: AsyncClient, bad_timeout: int) -> None:
    cl = await _auth_client(client)
    r = await client.post(
        f"/services/svc?request_id=x&timeout={bad_timeout}",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "TIMEOUT_OUT_OF_RANGE"


async def test_post_to_service_bumps_client_last_seen(app: object, client: AsyncClient) -> None:
    cl = await _auth_client(client)
    registry = app.state.client_registry  # type: ignore[attr-defined]
    entry = registry.get(cl["client_id"])
    assert entry is not None
    before = entry.last_seen
    await asyncio.sleep(0.02)
    r = await client.post(
        "/services/billing?request_id=r1",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert r.status_code == 202
    assert entry.last_seen > before


async def test_post_defaults_content_type_when_absent(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "default-ct")
    # httpx adds a default Content-Type for `content=` of `application/octet-stream`,
    # so we explicitly clear it to test pype's own default.
    r = await client.post(
        "/services/default-ct?request_id=r1",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": ""},
        content=b"hi",
    )
    assert r.status_code == 202
    g = await client.get(
        "/services/default-ct?timeout=500", headers=auth_header(str(svc["access_token"]))
    )
    assert g.status_code == 200
    assert g.headers["content-type"].startswith("application/json")


# ----------------------------------------------------------------------------
# block=1 (synchronous request/response in one POST) tests
# ----------------------------------------------------------------------------


async def test_block_mode_returns_200_with_response_when_service_responds(
    client: AsyncClient,
) -> None:
    cl = await _auth_client(client)
    svc = await _auth_service(client, "calc")

    async def fake_service() -> None:
        # Wait for the request, then post a response back addressed to that client.
        await asyncio.sleep(0.05)
        r = await client.get(
            "/services/calc?timeout=2000",
            headers=auth_header(str(svc["access_token"])),
        )
        assert r.status_code == 200
        client_id = r.headers["x-pype-client-id"]
        request_id = r.headers["x-pype-request-id"]
        await client.post(
            f"/clients/{client_id}?request_id={request_id}",
            headers={
                **auth_header(str(svc["access_token"])),
                "Content-Type": "text/plain",
            },
            content=b"computed-answer",
        )

    svc_task = asyncio.create_task(fake_service())
    r = await client.post(
        "/services/calc?request_id=q1&block=1&timeout=3000",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"input",
    )
    await svc_task

    assert r.status_code == 200
    assert r.content == b"computed-answer"
    assert r.headers["x-pype-request-id"] == "q1"
    assert r.headers["content-type"].startswith("text/plain")


async def test_block_mode_returns_202_when_no_response_in_time(client: AsyncClient) -> None:
    cl = await _auth_client(client)
    # No service ever pulls from this queue; no response will come back.
    r = await client.post(
        "/services/lonely?request_id=q1&block=1&timeout=100",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert r.status_code == 202
    assert r.content == b""


async def test_block_mode_returns_503_when_service_queue_full(client: AsyncClient) -> None:
    # Test settings: service_queue_max_size == 4. Fill the queue with non-blocking POSTs
    # first, then an overflowing block=1 POST should hit 503 in phase 1 and never reach
    # phase 2.
    cl = await _auth_client(client)
    for i in range(4):
        await client.post(
            f"/services/full-svc?request_id=r{i}&timeout=0",
            headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
            content=b"x",
        )
    overflow = await client.post(
        "/services/full-svc?request_id=overflow&block=1&timeout=0",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert overflow.status_code == 503


async def test_block_mode_returns_200_with_mismatched_request_id_when_stale_response_present(
    client: AsyncClient,
) -> None:
    """Phase 2 dequeues the head of the client's queue regardless of request_id.
    If a previous request has a response sitting in the client's queue, that response
    will be returned to *this* POST, with X-Pype-Request-Id correctly identifying the
    earlier request. The client library is responsible for noticing the mismatch."""
    cl = await _auth_client(client)
    svc = await _auth_service(client, "echo")

    # Step 1: client POSTs request "r0" non-blockingly, service responds, response sits
    # in the client's queue waiting.
    await client.post(
        "/services/echo?request_id=r0",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"first",
    )
    drained = await client.get(
        "/services/echo?timeout=1000", headers=auth_header(str(svc["access_token"]))
    )
    await client.post(
        f"/clients/{cl['client_id']}"
        f"?request_id={drained.headers['x-pype-request-id']}",
        headers={**auth_header(str(svc["access_token"])), "Content-Type": "text/plain"},
        content=b"FIRST_RESPONSE",
    )

    # Step 2: client POSTs a NEW request with block=1. Phase 1 enqueues r1 onto the
    # service queue, then phase 2 dequeues from the client's queue and gets r0's
    # response back even though we asked about r1.
    r = await client.post(
        "/services/echo?request_id=r1&block=1&timeout=500",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"second",
    )
    assert r.status_code == 200
    assert r.headers["x-pype-request-id"] == "r0"  # NOT r1 — this is the key assertion
    assert r.content == b"FIRST_RESPONSE"


async def test_block_mode_with_zero_timeout_non_blocking_full_path(client: AsyncClient) -> None:
    # timeout=0: enqueue with no wait, then dequeue with no wait. Three outcomes
    # depending on the moment we hit. This covers the simplest "queued but nothing
    # ready" outcome.
    cl = await _auth_client(client)
    r = await client.post(
        "/services/never-listened?request_id=z1&block=1&timeout=0",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert r.status_code == 202


async def test_block_absent_preserves_legacy_202_behavior(client: AsyncClient) -> None:
    # Sanity check: the existing protocol is unchanged when block is absent.
    cl = await _auth_client(client)
    r = await client.post(
        "/services/legacy?request_id=l1",
        headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
        content=b"x",
    )
    assert r.status_code == 202
    assert r.content == b""
