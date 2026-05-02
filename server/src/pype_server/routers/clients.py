from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status

from pype_server.config import Settings, get_settings
from pype_server.deps import ClientRegistryDep
from pype_server.exceptions import ForbiddenError
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
        503: {"description": "Client queue full; timed out before space was available"},
    },
)
async def post_response_to_client(
    request: Request,
    client_id: Annotated[str, Path(min_length=1)],
    claims: Annotated[ServiceClaims, Depends(service_claims)],
    registry: ClientRegistryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    request_id: Annotated[str, Query(min_length=1)],
    timeout: Annotated[int | None, Query()] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> Response:
    # `client_id` is unguessable (256-bit random) and the service token's signature is
    # validated by the dependency above, so authority is established. The entry itself
    # is lazy-created — if this is the first time anyone has touched this client_id on
    # this pype instance (e.g., the client first authenticated against a different
    # instance and bounced over here), we just create a fresh queue. The reaper will
    # clean it up later if the client never comes back to drain it.
    entry = registry.get_or_create(client_id)

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
    },
)
async def get_response_for_client(
    client_id: Annotated[str, Path(min_length=1)],
    claims: Annotated[ClientClaims, Depends(client_claims)],
    registry: ClientRegistryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    timeout: Annotated[int | None, Query()] = None,
) -> Response:
    if claims["client_id"] != client_id:
        raise ForbiddenError("path client_id does not match token's client_id")
    # Lazy-create: if this client just authenticated and is polling before any service
    # has POSTed a response (or the client landed here from another pype instance), we
    # still need an empty queue to long-poll on. JWT signature is the sole authority;
    # entry presence is just bookkeeping.
    entry = registry.get_or_create(client_id)

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
