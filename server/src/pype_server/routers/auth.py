from typing import Annotated

from fastapi import APIRouter, Depends, status

from pype_server.config import Settings, get_settings
from pype_server.deps import ClientRegistryDep, ServiceRegistryDep
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
    # client_id is itself the capability: a 256-bit random URL-safe string. Knowing it
    # is what proves authority over the client's queue, so there's no separate secret.
    client_id = generate_secret()
    registry.register(client_id)
    token = issue_client_jwt(body.name, client_id, settings)
    return ClientAuthResponse(access_token=token, name=body.name, client_id=client_id)


@router.delete("/client", response_model=ClientLogoffResponse, status_code=status.HTTP_200_OK)
async def client_logoff(
    claims: Annotated[ClientClaims, Depends(client_claims)],
    registry: ClientRegistryDep,
) -> ClientLogoffResponse:
    # Idempotent: if the queue is already gone (reaped or previously deleted), treat as
    # logged off. The JWT signature alone is sufficient authority to drop our own queue;
    # the only check needed is that the entry exists at the JWT's client_id.
    registry.remove(claims["client_id"])
    return ClientLogoffResponse(client_id=claims["client_id"])
