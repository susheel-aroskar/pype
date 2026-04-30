from typing import Annotated

from fastapi import APIRouter, Depends, status

from pype_server.config import Settings, get_settings
from pype_server.deps import ClientRegistryDep, ServiceRegistryDep
from pype_server.exceptions import ForbiddenError
from pype_server.schemas.auth import (
    ClientAuthRequest,
    ClientAuthResponse,
    ClientLogoffResponse,
    ServiceAuthRequest,
    ServiceAuthResponse,
)
from pype_server.security import (
    ClientClaims,
    client_claims,
    generate_secret,
    issue_client_jwt,
    issue_service_jwt,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/service", response_model=ServiceAuthResponse, status_code=status.HTTP_200_OK)
async def service_auth(
    body: ServiceAuthRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    service_registry: ServiceRegistryDep,
) -> ServiceAuthResponse:
    service_secret = generate_secret()
    token = issue_service_jwt(body.service_name, service_secret, settings)
    service_registry.get_or_create(body.service_name)
    return ServiceAuthResponse(
        access_token=token, name=body.service_name, service_secret=service_secret
    )


@router.post("/client", response_model=ClientAuthResponse, status_code=status.HTTP_200_OK)
async def client_auth(
    body: ClientAuthRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    registry: ClientRegistryDep,
) -> ClientAuthResponse:
    client_secret = generate_secret()
    entry = registry.register(client_secret)
    token = issue_client_jwt(body.name, entry.client_id, client_secret, settings)
    return ClientAuthResponse(
        access_token=token,
        name=body.name,
        client_id=entry.client_id,
        client_secret=client_secret,
    )


@router.delete("/client", response_model=ClientLogoffResponse, status_code=status.HTTP_200_OK)
async def client_logoff(
    claims: Annotated[ClientClaims, Depends(client_claims)],
    registry: ClientRegistryDep,
) -> ClientLogoffResponse:
    entry = registry.get(claims["client_id"])
    if entry is None:
        # Idempotent: if the queue is already gone (reaped or previously deleted), treat
        # as logged off. The client's goal is achieved either way.
        return ClientLogoffResponse(client_id=claims["client_id"])
    if entry.client_secret != claims["client_secret"]:
        raise ForbiddenError("client_secret mismatch")
    registry.remove(claims["client_id"])
    return ClientLogoffResponse(client_id=claims["client_id"])
