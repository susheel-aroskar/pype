from httpx import AsyncClient

from tests.conftest import auth_header


async def test_client_auth_returns_jwt_and_claims(client: AsyncClient) -> None:
    r = await client.post("/auth/client", json={"name": "alice"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["role"] == "client"
    assert body["name"] == "alice"
    assert isinstance(body["client_id"], int) and body["client_id"] >= 1
    assert isinstance(body["client_secret"], str) and len(body["client_secret"]) >= 32
    assert isinstance(body["access_token"], str) and body["access_token"]


async def test_client_ids_are_unique(client: AsyncClient) -> None:
    a = (await client.post("/auth/client", json={"name": "a"})).json()
    b = (await client.post("/auth/client", json={"name": "b"})).json()
    assert a["client_id"] != b["client_id"]
    assert a["client_secret"] != b["client_secret"]


async def test_client_logoff_succeeds(client: AsyncClient) -> None:
    auth = (await client.post("/auth/client", json={"name": "alice"})).json()
    r = await client.delete("/auth/client", headers=auth_header(auth["access_token"]))
    assert r.status_code == 200
    assert r.json() == {"client_id": auth["client_id"], "status": "logged_off"}
    # After logoff the queue should be gone — GET should now return 400.
    r2 = await client.get(
        f"/clients/{auth['client_id']}?timeout=0", headers=auth_header(auth["access_token"])
    )
    assert r2.status_code == 400


async def test_client_logoff_idempotent(client: AsyncClient) -> None:
    auth = (await client.post("/auth/client", json={"name": "alice"})).json()
    r1 = await client.delete("/auth/client", headers=auth_header(auth["access_token"]))
    r2 = await client.delete("/auth/client", headers=auth_header(auth["access_token"]))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert (
        r1.json()
        == r2.json()
        == {
            "client_id": auth["client_id"],
            "status": "logged_off",
        }
    )


async def test_client_logoff_requires_jwt(client: AsyncClient) -> None:
    r = await client.delete("/auth/client")
    assert r.status_code == 401


async def test_client_logoff_rejects_service_token(client: AsyncClient) -> None:
    s = (await client.post("/auth/service", json={"service_name": "svc"})).json()
    r = await client.delete("/auth/client", headers=auth_header(s["access_token"]))
    assert r.status_code == 400
