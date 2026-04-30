from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status

from pype_server.config import Settings, get_settings
from pype_server.deps import ClientRegistryDep
from pype_server.exceptions import (
    ClientQueueGoneError,
    ClientQueueNotFoundError,
    ForbiddenError,
)
from pype_server.messaging import PypeResponse
from pype_server.queueing import normalize_timeout, queue_get, queue_put
from pype_server.security import ClientClaims, ServiceClaims, client_claims, service_claims

router = APIRouter(prefix="/clients", tags=["clients"])


@router.post(
    "/{client_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={
        202: {"description": "Service response enqueued"},
        410: {"description": "Client no longer exists"},
        503: {"description": "Client queue full; timed out before space was available"},
    },
)
async def post_response_to_client(
    request: Request,
    client_id: Annotated[int, Path()],
    claims: Annotated[ServiceClaims, Depends(service_claims)],
    registry: ClientRegistryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    client_secret: Annotated[str, Query(min_length=1)],
    request_id: Annotated[str, Query(min_length=1)],
    timeout: Annotated[int | None, Query()] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> Response:
    entry = registry.get(client_id)
    if entry is None:
        raise ClientQueueGoneError(f"no client queue for client_id={client_id}")
    if entry.client_secret != client_secret:
        raise ForbiddenError("client_secret mismatch")

    timeout_ms = normalize_timeout(timeout, settings.max_timeout_ms)
    payload = await request.body()
    pype_resp = PypeResponse(
        request_id=request_id,
        content_type=content_type or settings.default_content_type,
        payload=payload,
    )
    enqueued = await queue_put(entry.queue, pype_resp, timeout_ms)
    if not enqueued:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.get(
    "/{client_id}",
    response_class=Response,
    responses={
        200: {"description": "Service response dequeued"},
        204: {"description": "Client queue empty; timed out"},
        400: {"description": "Client does not exist"},
    },
)
async def get_response_for_client(
    client_id: Annotated[int, Path()],
    claims: Annotated[ClientClaims, Depends(client_claims)],
    registry: ClientRegistryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    timeout: Annotated[int | None, Query()] = None,
) -> Response:
    if claims["client_id"] != client_id:
        raise ForbiddenError("path client_id does not match token's client_id")
    entry = registry.get(client_id)
    if entry is None:
        raise ClientQueueNotFoundError(f"no client registered for client_id={client_id}")
    if entry.client_secret != claims["client_secret"]:
        raise ForbiddenError("client_secret mismatch")

    # GET proves liveness regardless of whether a response is dequeued.
    registry.bump_last_seen(client_id)

    timeout_ms = normalize_timeout(timeout, settings.max_timeout_ms)
    item = await queue_get(entry.queue, timeout_ms)

    # Bump again at the end as well; a long block doesn't surprise the reaper if/when it
    # runs after we awake. Cheap, idempotent.
    registry.bump_last_seen(client_id)

    if item is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=item.payload,
        media_type=item.content_type,
        headers={"X-Pype-Request-Id": item.request_id},
    )
