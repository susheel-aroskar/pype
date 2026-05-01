"""ServiceClient + ServiceConnection — the service-side surface of pype-client.

Used by backend services to authenticate, pull client requests, and POST responses.
Not thread-safe: instantiate one ServiceConnection per thread if you need concurrency.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

import requests

from pype_client._http import call, http_timeout_seconds, raise_for_status
from pype_client.config import Settings, get_settings
from pype_client.exceptions import PypeProtocolError
from pype_client.messages import ClientRequest
from pype_client.schemas import ServiceAuthRequest


class ServiceClient:
    """Builds `ServiceConnection`s by authenticating against the pype server."""

    def __init__(
        self,
        base_url: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._base_url = (base_url or self._settings.base_url).rstrip("/")

    def authenticate(self, auth_request: ServiceAuthRequest) -> ServiceConnection:
        """POST /auth/service and return a ServiceConnection on success.

        `service_name` is taken from `auth_request` and echoed back in the auth response;
        the returned `ServiceConnection` caches it for use by `get_request()`.
        """
        session = requests.Session()
        try:
            # Auth is non-blocking on the server, so we use auth_timeout_ms directly without
            # adding network_buffer_ms.
            resp = call(
                session,
                "POST",
                f"{self._base_url}/auth/service",
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
            name = data["name"]
        except (KeyError, TypeError) as exc:
            session.close()
            raise PypeProtocolError(f"malformed auth response: {data!r}") from exc

        session.headers["Authorization"] = f"Bearer {access_token}"
        # Echo the server's identifying IP on every subsequent request through this
        # session, so a future LB / reverse proxy in front of pype can route stickily
        # back to the same instance (which holds the relevant queues in memory).
        # Best-effort: if the header is missing, we just don't set it.
        server_internal_ip = resp.headers.get("X-Pype-Server-IP")
        if server_internal_ip:
            session.headers["X-Pype-Server-IP"] = server_internal_ip
        return ServiceConnection(
            base_url=self._base_url,
            service_name=name,
            access_token=access_token,
            server_internal_ip=server_internal_ip,
            session=session,
            settings=self._settings,
        )


class ServiceConnection:
    """An authenticated service-side connection. Long-lived `requests.Session` underneath."""

    def __init__(
        self,
        base_url: str,
        service_name: str,
        access_token: str,
        session: requests.Session,
        settings: Settings,
        server_internal_ip: str | None = None,
    ) -> None:
        self._base_url = base_url
        self.service_name = service_name
        self.access_token = access_token
        self.server_internal_ip = server_internal_ip
        self._session = session
        self._settings = settings
        self._closed = False

    def get_request(self, timeout: int | None = None) -> ClientRequest:
        """GET /services/{service_name}. Block up to `timeout` ms (None = server max).

        Raises `PypeTimeoutError` if the server's queue is empty for the full timeout and
        returns 204. Raises `PypeAuthError` / `PypeForbiddenError` etc. on auth failures.
        """
        self._ensure_open()
        params: dict[str, int] = {}
        if timeout is not None:
            params["timeout"] = timeout
        resp = call(
            self._session,
            "GET",
            f"{self._base_url}/services/{self.service_name}",
            timeout_seconds=http_timeout_seconds(timeout, self._settings.network_buffer_ms),
            params=params,
        )
        raise_for_status(resp, expected_codes={200, 204})
        if resp.status_code == 204:
            from pype_client.exceptions import PypeTimeoutError

            raise PypeTimeoutError(
                f"no requests available for service {self.service_name!r} within timeout"
            )
        try:
            client_id = resp.headers["X-Pype-Client-Id"]
            request_id = resp.headers["X-Pype-Request-Id"]
        except KeyError as exc:
            raise PypeProtocolError(f"missing X-Pype-* header: {exc}") from exc
        return ClientRequest(
            client_id=client_id,
            request_id=request_id,
            content_type=resp.headers.get("Content-Type", "application/octet-stream"),
            payload=resp.content,
            service_name=self.service_name,
            connection=self,
        )

    def _send_response(
        self,
        for_request: ClientRequest,
        response: str | bytes,
        content_type: str,
        timeout: int | None,
    ) -> None:
        """Internal: POST /clients/{client_id} on behalf of a `ClientRequest.send_response()`."""
        self._ensure_open()
        body = response.encode("utf-8") if isinstance(response, str) else response
        params: dict[str, int | str] = {"request_id": for_request.request_id}
        if timeout is not None:
            params["timeout"] = timeout
        resp = call(
            self._session,
            "POST",
            f"{self._base_url}/clients/{for_request.client_id}",
            timeout_seconds=http_timeout_seconds(timeout, self._settings.network_buffer_ms),
            params=params,
            data=body,
            headers={"Content-Type": content_type},
        )
        raise_for_status(resp, expected_codes={202})

    def close(self) -> None:
        """Best-effort cleanup. Services have no logoff endpoint per the spec, so this just
        closes the underlying HTTP session and connection pool."""
        if self._closed:
            return
        self._closed = True
        self._session.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ServiceConnection is closed")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
