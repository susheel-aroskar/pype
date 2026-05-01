from httpx import AsyncClient


async def test_service_auth_returns_jwt_and_claim_fields(client: AsyncClient) -> None:
    r = await client.post("/auth/service", json={"service_name": "billing"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["role"] == "service"
    assert body["name"] == "billing"
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert isinstance(body["service_secret"], str) and len(body["service_secret"]) >= 32
    assert r.headers["X-Pype-Server-IP"] == "10.42.0.1"


async def test_service_auth_registers_queue_lazily(client: AsyncClient) -> None:
    r = await client.post("/auth/service", json={"service_name": "shipping"})
    assert r.status_code == 200
    load = (await client.get("/load/services")).json()
    assert {"service_name": "shipping", "load": 0} in load


async def test_service_auth_rejects_empty_service_name(client: AsyncClient) -> None:
    r = await client.post("/auth/service", json={"service_name": ""})
    assert r.status_code == 422


async def test_multiple_instances_get_distinct_secrets_same_name(client: AsyncClient) -> None:
    a = (await client.post("/auth/service", json={"service_name": "x"})).json()
    b = (await client.post("/auth/service", json={"service_name": "x"})).json()
    assert a["name"] == b["name"] == "x"
    assert a["service_secret"] != b["service_secret"]
    assert a["access_token"] != b["access_token"]
