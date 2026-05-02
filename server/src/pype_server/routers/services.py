import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status

from pype_server.config import Settings, get_settings
from pype_server.deps import ClientRegistryDep, ServiceRegistryDep
from pype_server.exceptions import RoleMismatchError
from pype_server.messaging import PypeRequest
from pype_server.queueing import normalize_timeout, queue_get, queue_put
from pype_server.security import ClientClaims, ServiceClaims, client_claims, service_claims

router = APIRouter(prefix="/services", tags=["services"])


@router.post(
    "/{service_name}",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={
        200: {
            "description": (
                "(block=1 only) A response was dequeued from the client's queue while "
                "this POST was waiting. The body carries the response payload; "
                "X-Pype-Request-Id identifies which request the response corresponds to "
                "(may differ from the request_id of THIS POST if an earlier request's "
                "response was already waiting in the client's queue)."
            )
        },
        202: {"description": "Client request enqueued onto the service queue"},
        503: {"description": "Service queue full; timed out before space available"},
    },
)
async def post_request_to_service(
    request: Request,
    service_name: Annotated[str, Path(min_length=1)],
    claims: Annotated[ClientClaims, Depends(client_claims)],
    registry: ClientRegistryDep,
    service_registry: ServiceRegistryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    request_id: Annotated[str, Query(min_length=1)],
    timeout: Annotated[int | None, Query()] = None,
    block: Annotated[bool, Query()] = False,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> Response:
    # JWT signature already validated by `client_claims` above. The client_id is a
    # 256-bit random unguessable string, signed by us — no further authority check is
    # needed. The client_registry entry is lazy-created so a client whose JWT was issued
    # by another pype instance (in a clustered deployment) gets a fresh queue here on
    # demand.
    entry = registry.get_or_create(claims["client_id"])

    # POSTing a request proves client liveness. Bump before the (potentially blocking)
    # enqueue so the reaper doesn't evict mid-call, and again after so the next call has
    # a full inactivity window before the reaper can run.
    registry.bump_last_seen(claims["client_id"])

    total_timeout_ms = normalize_timeout(timeout, settings.max_timeout_ms)
    payload = await request.body()
    pype_req = PypeRequest(
        client_id=claims["client_id"],
        request_id=request_id,
        content_type=content_type or settings.default_content_type,
        payload=payload,
    )

    # Both phases share one deadline so the user-supplied `timeout` is honored end-to-end.
    deadline = time.monotonic() + total_timeout_ms / 1000.0

    # ── Phase 1: enqueue onto the service queue ──────────────────────────────────
    queue = service_registry.get_or_create(service_name)
    enqueued = await queue_put(queue, pype_req, _remaining_ms(deadline))

    registry.bump_last_seen(claims["client_id"])

    if not enqueued:
        # Service queue stayed full for the whole timeout. Request was NOT accepted.
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    if not block:
        # Plain non-blocking POST: the request is queued; client will pull the response
        # later via GET /clients/{client_id}.
        return Response(status_code=status.HTTP_202_ACCEPTED)

    # ── Phase 2 (block=1 only): wait on the client's response queue ──────────────
    # Note: this dequeue is NOT filtered by request_id. It returns whichever response
    # is at the head of the client's queue — possibly the response to some earlier
    # request from the same client. The X-Pype-Request-Id header tells the client
    # which response it actually got; the client library is responsible for noticing
    # a mismatch and looping via GET /clients/{client_id} to find its actual answer.
    response = await queue_get(entry.queue, _remaining_ms(deadline))
    if response is None:
        # Request is in flight on the service queue, but no response landed in time.
        # The client should fall back to GET /clients/{client_id}.
        return Response(status_code=status.HTTP_202_ACCEPTED)

    return Response(
        content=response.payload,
        media_type=response.content_type,
        headers={"X-Pype-Request-Id": response.request_id},
    )


def _remaining_ms(deadline_monotonic: float) -> int:
    """Milliseconds left until `deadline_monotonic`, clamped at 0 (never negative).

    A 0 budget means "do not block" — that's correct in this handler since by then
    we've already enqueued, so trying a non-blocking dequeue is the right last act
    before returning 202.
    """
    return max(0, int((deadline_monotonic - time.monotonic()) * 1000))


@router.get(
    "/{service_name}",
    response_class=Response,
    responses={
        200: {"description": "Client request dequeued"},
        204: {"description": "Service queue empty; timed out"},
    },
)
async def get_request_for_service(
    service_name: Annotated[str, Path(min_length=1)],
    claims: Annotated[ServiceClaims, Depends(service_claims)],
    service_registry: ServiceRegistryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    timeout: Annotated[int | None, Query()] = None,
) -> Response:
    if claims["name"] != service_name:
        raise RoleMismatchError("token's service name does not match path")

    timeout_ms = normalize_timeout(timeout, settings.max_timeout_ms)
    queue = service_registry.get_or_create(service_name)
    item = await queue_get(queue, timeout_ms)
    if item is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=item.payload,
        media_type=item.content_type,
        headers={
            "X-Pype-Client-Id": item.client_id,
            "X-Pype-Request-Id": item.request_id,
        },
    )
