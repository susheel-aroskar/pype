"""Public message types returned by the client library.

`ClientRequest` wraps a request pulled by a service from `GET /services/{service_name}`.
`ServiceResponse` wraps a response pulled by a client from `GET /clients/{client_id}`.

Both expose:
- `.request_id`: str — the per-client identifier set by the original POST.
- `.content_type`: str — the standard HTTP `Content-Type` header preserved end-to-end.
- `.bytes`: bytes — the raw payload as-is (alias for the underlying `payload`).
- `.text`: str (lazy) — `payload` decoded as UTF-8; cached after first access.
- `.json()`: any (lazy) — `payload` parsed as JSON; cached after first call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pype_client._payload import _PayloadAccessors

if TYPE_CHECKING:
    from pype_client.service_client import ServiceConnection


class ClientRequest(_PayloadAccessors):
    """A request dequeued by a backend service from `GET /services/{service_name}`."""

    client_id: str
    request_id: str
    content_type: str
    payload: bytes
    service_name: str

    def __init__(
        self,
        client_id: str,
        request_id: str,
        content_type: str,
        payload: bytes,
        service_name: str,
        connection: ServiceConnection,
    ) -> None:
        super().__init__()
        self.client_id = client_id
        self.request_id = request_id
        self.content_type = content_type
        self.payload = payload
        self.service_name = service_name
        self._connection = connection

    def send_response(
        self,
        response: str | bytes,
        content_type: str = "application/json",
        timeout: int | None = None,
    ) -> None:
        """POST `response` back to `/clients/{client_id}` for this request_id.

        - `response` may be `str` (encoded as UTF-8) or `bytes` (sent as-is).
        - `timeout` in milliseconds: 0 = non-blocking; positive = block up to this many ms;
          None = block up to the server's max.
        - Raises a `PypeClientError` subclass on non-202 status (410, 400, 403, 503).
        """
        self._connection._send_response(self, response, content_type, timeout)


class ServiceResponse(_PayloadAccessors):
    """A response dequeued by a client from `GET /clients/{client_id}`.

    Returned by `ClientConnection.get_response*()` methods.
    """

    request_id: str
    content_type: str
    payload: bytes

    def __init__(self, request_id: str, content_type: str, payload: bytes) -> None:
        super().__init__()
        self.request_id = request_id
        self.content_type = content_type
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"ServiceResponse(request_id={self.request_id!r}, "
            f"content_type={self.content_type!r}, payload=<{len(self.payload)} bytes>)"
        )
