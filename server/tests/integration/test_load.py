from httpx import AsyncClient

from tests.conftest import auth_header


async def test_load_endpoint_no_auth_required(client: AsyncClient) -> None:
    r = await client.get("/load/services")
    assert r.status_code == 200
    assert r.json() == []


async def test_load_reports_queue_lengths(client: AsyncClient) -> None:
    cl = (await client.post("/auth/client", json={"name": "x"})).json()
    await client.post("/auth/service", json={"service_name": "alpha"})
    await client.post("/auth/service", json={"service_name": "beta"})
    # Push two messages onto alpha, none onto beta.
    for i in range(2):
        await client.post(
            f"/services/alpha?request_id=r{i}",
            headers={**auth_header(str(cl["access_token"])), "Content-Type": "text/plain"},
            content=b"x",
        )
    r = await client.get("/load/services")
    assert r.status_code == 200
    by_name = {entry["service_name"]: entry["load"] for entry in r.json()}
    assert by_name == {"alpha": 2, "beta": 0}
