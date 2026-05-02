import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pype_server.config import Settings, get_settings
from pype_server.exceptions import PypeError
from pype_server.reaper import ClientQueueReaper, run_reaper
from pype_server.registries import ClientRegistry, ServiceRegistry
from pype_server.routers import auth, clients, load, services

logger = logging.getLogger(__name__)


def _pype_error_handler(_: Request, exc: PypeError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code}
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    client_registry = ClientRegistry(queue_max_size=cfg.client_queue_max_size)
    service_registry = ServiceRegistry(queue_max_size=cfg.service_queue_max_size)
    reaper = ClientQueueReaper(
        registry=client_registry,
        period_seconds=cfg.client_reaper_period_seconds,
        batch_size=cfg.client_reaper_batch_size,
        inactivity_threshold_seconds=cfg.client_inactivity_threshold_seconds,
        target_sweep_seconds=cfg.client_reaper_target_sweep_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with run_reaper(reaper):
            yield

    app = FastAPI(title="pype", version="0.1.0", lifespan=lifespan)
    app.state.settings = cfg
    app.state.client_registry = client_registry
    app.state.service_registry = service_registry
    app.state.reaper = reaper

    # Routers depend on get_settings() (the lru_cached default factory). Override it so the
    # explicit `cfg` passed into create_app is what handlers see — important for test isolation
    # and for running multiple app instances with different config in the same process.
    app.dependency_overrides[get_settings] = lambda: cfg

    app.add_exception_handler(PypeError, _pype_error_handler)  # type: ignore[arg-type]

    app.include_router(auth.router)
    app.include_router(services.router)
    app.include_router(clients.router)
    app.include_router(load.router)

    return app


app = create_app()
