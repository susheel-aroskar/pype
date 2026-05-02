from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pype_server.config import Settings
from pype_server.main import create_app


def make_test_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "client_queue_max_size": 4,
        "service_queue_max_size": 4,
        "max_timeout_ms": 5_000,
        # Reaper period is large by default so it doesn't fire mid-test.
        # Tests that exercise the reaper instantiate ClientQueueReaper directly.
        "client_reaper_period_seconds": 3600.0,
        "client_reaper_batch_size": 1000,
        "client_reaper_target_sweep_seconds": 3600.0,
        "client_inactivity_threshold_seconds": 3600.0,
        # Pin internal_ip so tests don't make real DNS / socket calls and so assertions
        # against the X-Pype-Server-IP header have a deterministic value to compare.
        "internal_ip": "10.42.0.1",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def app() -> FastAPI:
    # We deliberately skip lifespan in unit/integration tests of the HTTP surface; the reaper
    # has its own dedicated tests. Building the app directly keeps tests fast and deterministic.
    return create_app(settings=make_test_settings())


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient) -> tuple[AsyncClient, dict[str, object]]:
    r = await client.post("/auth/client", json={"name": "tester"})
    r.raise_for_status()
    return client, r.json()


@pytest_asyncio.fixture
async def authed_service(client: AsyncClient) -> tuple[AsyncClient, dict[str, object]]:
    r = await client.post("/auth/service", json={"service_name": "echo"})
    r.raise_for_status()
    return client, r.json()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
