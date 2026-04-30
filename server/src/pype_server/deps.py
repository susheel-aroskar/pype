from typing import Annotated

from fastapi import Depends, Request

from pype_server.registries import ClientRegistry, ServiceRegistry


def get_client_registry(request: Request) -> ClientRegistry:
    return request.app.state.client_registry  # type: ignore[no-any-return]


def get_service_registry(request: Request) -> ServiceRegistry:
    return request.app.state.service_registry  # type: ignore[no-any-return]


ClientRegistryDep = Annotated[ClientRegistry, Depends(get_client_registry)]
ServiceRegistryDep = Annotated[ServiceRegistry, Depends(get_service_registry)]
