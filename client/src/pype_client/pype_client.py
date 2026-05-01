"""PypeClient + ClientConnection — the caller-side surface of pype-client.

Used by callers/clients to authenticate, POST requests to services, and pull responses.
Not thread-safe: instantiate one ClientConnection per thread if you need concurrency.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from types import TracebackType
from typing import Self

import requests

from pype_client._http import call, http_timeout_seconds, raise_for_status
from pype_client.config import Settings, get_settings
from pype_client.exceptions import (
    PypeClientError,
    PypeProtocolError,
    PypeTimeoutError,
)
from pype_client.messages import ServiceResponse
from pype_client.schemas import ClientAuthRequest

logger = logging.getLogger(__name__)


class PypeClient:
    """Builds `ClientConnection`s by authenticating against the pype server."""

    def __init__(self, base_url: str | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base_url = (base_url or self._settings.base_url).rstrip("/")

    def authenticate(self, auth_request: ClientAuthRequest) -> ClientConnection:
        """POST /auth/client and return a ClientConnection on success."""
        session = requests.Session()
        try:
            # Auth is non-blocking on the server, so we use auth_timeout_ms directly without
            # adding network_buffer_ms.
            resp = call(
                session,
                "POST",
                f"{self._base_url}/auth/client",
                timeout_seconds=self._settings.auth_timeout_ms / 1000.0,
                json=auth_request.model_dump(),
            )
            raise_for_status(resp, expected_codes={200})
            data = resp.json()
        except Exception:
            session.close()
            raise

        try:
            access_token = data["access_token"]
            client_id = data["client_id"]
            name = data["name"]
            if not isinstance(client_id, str) or not client_id:
                raise TypeError("client_id must be a non-empty string")
        except (KeyError, TypeError) as exc:
            session.close()
            raise PypeProtocolError(f"malformed auth response: {data!r}") from exc

        session.headers["Authorization"] = f"Bearer {access_token}"
        return ClientConnection(
            base_url=self._base_url,
            name=name,
            client_id=client_id,
            access_token=access_token,
            session=session,
            settings=self._settings,
        )


class ClientConnection:
    """An authenticated client-side connection. Holds an internal cache of out-of-order
    responses keyed by request_id, and a counter for generating unique request_ids.

    NOT thread-safe. Single-threaded blocking use only.
    """

    def __init__(
        self,
        base_url: str,
        name: str,
        client_id: str,
        access_token: str,
        session: requests.Session,
        settings: Settings,
    ) -> None:
        self._base_url = base_url
        self.name = name
        self.client_id = client_id
        self.access_token = access_token
        self._session = session
        self._settings = settings
        self._closed = False
        self._next_request_id: int = 0
        # request_id → ServiceResponse for out-of-order responses pulled by other get_*().
        self._cache: dict[str, ServiceResponse] = {}

    # ------------------------------------------------------------------ send

    def send_request(
        self,
        service_name: str,
        request: str | bytes,
        content_type: str = "application/json",
        timeout: int | None = None,
    ) -> str:
        """POST a request to /services/{service_name}. Returns the generated request_id.

        - `request` is `str` (encoded UTF-8) or `bytes` (sent as-is).
        - `timeout` is one-shot: if the server's queue is full and stays full for `timeout`
          ms, the server returns 503 and this method raises `PypeTimeoutError`.
        - `timeout=None` means "use the server's max"; we don't loop on send.
        """
        self._ensure_open()
        request_id = self._mint_request_id()
        body = request.encode("utf-8") if isinstance(request, str) else request
        params: dict[str, int | str] = {"request_id": request_id}
        if timeout is not None:
            params["timeout"] = timeout
        resp = call(
            self._session,
            "POST",
            f"{self._base_url}/services/{service_name}",
            timeout_seconds=http_timeout_seconds(timeout, self._settings.network_buffer_ms),
            params=params,
            data=body,
            headers={"Content-Type": content_type},
        )
        raise_for_status(resp, expected_codes={202})
        return request_id

    def call(
        self,
        service_name: str,
        request: str | bytes,
        content_type: str = "application/json",
        timeout: int | None = None,
    ) -> ServiceResponse:
        """Send a request and return its response in a single round-trip when possible.

        This is the preferred shape for simple synchronous RPC. Uses pype's `block=1`
        mode on POST /services/{name} so that, in the happy path, the server enqueues the
        request and turns around within the same HTTP request to dequeue this client's
        next response — saving one HTTP round-trip vs. `send_request` + `get_response`.

        For fan-out / scatter-gather / quorum patterns, prefer `send_request` followed by
        `get_any_responses` / `get_all_responses` / `get_response_quorum`.

        Args:
            service_name: name of the target backend service.
            request: payload as `str` (UTF-8 encoded) or raw `bytes`.
            content_type: HTTP `Content-Type` header for the request body.
            timeout: total ms allowed for both phases combined. `0` = non-blocking.
                `None` = block forever (loops past server timeouts, like `get_response`).

        Returns:
            A `ServiceResponse` for `request_id` (the one this method just minted).

        Raises:
            PypeTimeoutError: service queue stayed full for the timeout (request was
                NOT enqueued), or no matching response arrived within `timeout`.
            PypeAuthError, PypeForbiddenError, PypeBadRequestError: as for other methods.
        """
        self._ensure_open()
        request_id = self._mint_request_id()
        body = request.encode("utf-8") if isinstance(request, str) else request

        # One deadline shared across phase 1 (the block=1 POST) and phase 2 (the
        # fallback get_response loop). Matches the server's own deadline-sharing.
        deadline = _Deadline(timeout)
        remaining_ms = deadline.remaining_ms()

        # ── Phase 1: blocking POST with block=1 ─────────────────────────────────
        params: dict[str, int | str] = {"request_id": request_id, "block": 1}
        if remaining_ms is not None:
            # Negative remaining is a programming error here (we just minted the deadline).
            params["timeout"] = max(0, remaining_ms)
        resp = call(
            self._session,
            "POST",
            f"{self._base_url}/services/{service_name}",
            timeout_seconds=http_timeout_seconds(remaining_ms, self._settings.network_buffer_ms),
            params=params,
            data=body,
            headers={"Content-Type": content_type},
        )
        # 503 -> PypeTimeoutError (queue full). 200 -> a response was dequeued (might be
        # for some other request_id). 202 -> request enqueued, no response in time.
        raise_for_status(resp, expected_codes={200, 202})

        if resp.status_code == 200:
            returned_request_id = resp.headers.get("X-Pype-Request-Id")
            if returned_request_id is None:
                raise PypeProtocolError("missing X-Pype-Request-Id header on 200 response")
            response_object = ServiceResponse(
                request_id=returned_request_id,
                content_type=resp.headers.get("Content-Type", "application/octet-stream"),
                payload=resp.content,
            )
            if returned_request_id == request_id:
                # Happy path — one HTTP round-trip.
                return response_object
            # Server returned someone else's response (a stale entry in our queue).
            # Cache it; a subsequent get_response(<that_rid>) will return it.
            self._cache[returned_request_id] = response_object

        # ── Phase 2: fall back to the standard get_response loop ────────────────
        # Either we got a 202 (request enqueued, no response yet) or a 200-with-mismatch.
        # In both cases the request is now on the service queue and a response will
        # arrive in our client queue at some point. Reuse the existing get_response
        # logic — it knows how to drain stale entries, cache mismatches, and honor
        # `timeout=None` as "loop forever".
        remaining_ms = deadline.remaining_ms()
        if remaining_ms is None:
            return self.get_response(request_id, timeout=None)
        if remaining_ms < 0:
            raise PypeTimeoutError("local deadline elapsed before response arrived")
        return self.get_response(request_id, timeout=remaining_ms)

    # ------------------------------------------------------------------ get_response variants

    def get_response(self, request_id: str, timeout: int | None = None) -> ServiceResponse:
        """Wait until a response for `request_id` is available, then return it.

        Pulls one response per server call. Responses for other request_ids encountered
        along the way are cached for later retrieval. With finite `timeout`, raises
        `PypeTimeoutError` on local deadline elapse OR server 204; with `timeout=None`,
        loops indefinitely past server 204s. Cached responses are NOT dropped on timeout.
        """
        self._ensure_open()
        cached = self._cache.pop(request_id, None)
        if cached is not None:
            return cached

        infinite = timeout is None
        deadline = _Deadline(timeout)
        while True:
            resp = self._poll_one(deadline.remaining_ms())
            if resp is None:
                # Server returned 204. With finite timeout, that means our budget is up.
                if not infinite:
                    raise PypeTimeoutError("server queue empty within timeout")
                continue
            if resp.request_id == request_id:
                return resp
            self._cache[resp.request_id] = resp

    def get_any_responses(
        self, *request_ids: str, timeout: int | None = None
    ) -> dict[str, ServiceResponse]:
        """Return as soon as ANY of `*request_ids` is available. Returns ALL currently-matching
        ones (popped from cache). Raises `PypeTimeoutError` if no match by deadline."""
        self._ensure_open()
        if not request_ids:
            raise ValueError("at least one request_id is required")
        wanted = set(request_ids)

        def is_done() -> bool:
            return any(rid in self._cache for rid in wanted)

        self._loop_until(is_done, timeout)
        return self._pop_matching(wanted)

    def get_all_responses(
        self, *request_ids: str, timeout: int | None = None
    ) -> dict[str, ServiceResponse]:
        """Return only when ALL of `*request_ids` are available. Pops all of them."""
        self._ensure_open()
        if not request_ids:
            raise ValueError("at least one request_id is required")
        wanted = set(request_ids)

        def is_done() -> bool:
            return wanted.issubset(self._cache)

        self._loop_until(is_done, timeout)
        return self._pop_matching(wanted)

    def get_response_quorum(
        self, quorum: int, *request_ids: str, timeout: int | None = None
    ) -> dict[str, ServiceResponse]:
        """Return when at least `quorum` of `*request_ids` are available. Pops all
        currently-matching ones (size will be in `[quorum, len(request_ids)]`)."""
        self._ensure_open()
        if not request_ids:
            raise ValueError("at least one request_id is required")
        if quorum < 1 or quorum > len(request_ids):
            raise ValueError(f"quorum must be in [1, {len(request_ids)}]; got {quorum}")
        wanted = set(request_ids)

        def is_done() -> bool:
            return sum(1 for rid in wanted if rid in self._cache) >= quorum

        self._loop_until(is_done, timeout)
        return self._pop_matching(wanted)

    # ------------------------------------------------------------------ shared loop

    def _loop_until(self, is_done: Callable[[], bool], timeout: int | None) -> None:
        """Drive the polling loop until `is_done()` is true.

        On each iteration: poll one response from the server (or skip if cache satisfies
        already), cache it, check `is_done()`. With finite `timeout`, raises
        `PypeTimeoutError` on local deadline elapse OR server 204. With `timeout=None`,
        loops indefinitely past 204s.
        """
        if is_done():
            return
        infinite = timeout is None
        deadline = _Deadline(timeout)
        while True:
            resp = self._poll_one(deadline.remaining_ms())
            if resp is None:
                if not infinite:
                    raise PypeTimeoutError("server queue empty within timeout")
                continue
            self._cache[resp.request_id] = resp
            if is_done():
                return

    def _poll_one(self, remaining_ms: int | None) -> ServiceResponse | None:
        """One server call. Returns ServiceResponse on 200, None on server-side 204 (caller
        decides whether 204 is fatal). Raises `PypeTimeoutError` if the local deadline has
        already gone negative before the call. `remaining_ms == 0` is allowed and triggers
        a non-blocking server call (server returns immediately)."""
        if remaining_ms is not None and remaining_ms < 0:
            raise PypeTimeoutError("local deadline elapsed")
        params: dict[str, int] = {}
        if remaining_ms is not None:
            params["timeout"] = remaining_ms
        resp = call(
            self._session,
            "GET",
            f"{self._base_url}/clients/{self.client_id}",
            timeout_seconds=http_timeout_seconds(remaining_ms, self._settings.network_buffer_ms),
            params=params,
        )
        raise_for_status(resp, expected_codes={200, 204})
        if resp.status_code == 204:
            return None
        try:
            request_id = resp.headers["X-Pype-Request-Id"]
        except KeyError as exc:
            raise PypeProtocolError("missing X-Pype-Request-Id header on 200 response") from exc
        return ServiceResponse(
            request_id=request_id,
            content_type=resp.headers.get("Content-Type", "application/json"),
            payload=resp.content,
        )

    def _pop_matching(self, wanted: Iterable[str]) -> dict[str, ServiceResponse]:
        out: dict[str, ServiceResponse] = {}
        for rid in list(wanted):
            if rid in self._cache:
                out[rid] = self._cache.pop(rid)
        return out

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Best-effort logoff: DELETE /auth/client and close the HTTP session.

        Network errors and unexpected statuses are logged and swallowed — the user's intent
        is "I'm done", so a flaky server should not cause an exception out of close().
        After close(), further calls raise RuntimeError.
        """
        if self._closed:
            return
        self._closed = True
        try:
            resp = self._session.delete(
                f"{self._base_url}/auth/client",
                timeout=self._settings.auth_timeout_ms / 1000.0,
            )
            if resp.status_code not in (200, 204):
                logger.warning("logoff returned %s: %s", resp.status_code, resp.text[:200])
        except (PypeClientError, requests.exceptions.RequestException) as exc:
            logger.warning("logoff failed (best-effort, ignoring): %s", exc)
        finally:
            self._session.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ClientConnection is closed")

    def _mint_request_id(self) -> str:
        self._next_request_id += 1
        return str(self._next_request_id)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class _Deadline:
    """Tracks a wall-clock deadline. `remaining_ms()` returns None if the original timeout
    was None (block forever), otherwise the time left in ms (which can be 0 or negative)."""

    __slots__ = ("_deadline_monotonic",)

    def __init__(self, timeout_ms: int | None) -> None:
        if timeout_ms is None:
            self._deadline_monotonic: float | None = None
        else:
            self._deadline_monotonic = time.monotonic() + timeout_ms / 1000.0

    def remaining_ms(self) -> int | None:
        if self._deadline_monotonic is None:
            return None
        # Return signed: callers distinguish "0 ms left = OK to make one non-blocking call"
        # from "negative = deadline already passed, give up".
        return int((self._deadline_monotonic - time.monotonic()) * 1000)
