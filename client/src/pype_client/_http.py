"""Internal HTTP plumbing shared by ServiceConnection and ClientConnection.

Concentrates: status-code → exception mapping, request-library timeout calculation
(server-deadline + network buffer), and a single place to wrap requests-level network
errors into `PypeNetworkError`.
"""

from __future__ import annotations

from typing import Any

import requests

from pype_client.exceptions import (
    PypeAuthError,
    PypeBadRequestError,
    PypeClientGoneError,
    PypeForbiddenError,
    PypeNetworkError,
    PypeProtocolError,
    PypeTimeoutError,
)


def http_timeout_seconds(server_timeout_ms: int | None, network_buffer_ms: int) -> float | None:
    """Compute the timeout we pass to `requests` itself.

    Strategy:
    - If we tell the server `timeout=N ms`, our local socket timeout must be
      `(N + buffer) / 1000` seconds so the server can return its 204/503 cleanly before we
      cut the connection.
    - If `server_timeout_ms` is None (we asked the server to use its max), we don't know
      the exact server deadline; return None so `requests` waits indefinitely. The caller
      handles the higher-level loop semantics.
    """
    if server_timeout_ms is None:
        return None
    return (server_timeout_ms + network_buffer_ms) / 1000.0


def raise_for_status(resp: requests.Response, *, expected_codes: set[int]) -> None:
    """Map server statuses to PypeClientError subclasses. Caller passes acceptable statuses
    (e.g. {200, 204} for GET, {202} for POST). Anything else raises."""
    if resp.status_code in expected_codes:
        return
    status = resp.status_code
    detail = _describe(resp)
    if status == 401:
        raise PypeAuthError(detail)
    if status == 400:
        raise PypeBadRequestError(detail)
    if status == 403:
        raise PypeForbiddenError(detail)
    if status == 410:
        raise PypeClientGoneError(detail)
    if status == 503:
        raise PypeTimeoutError(detail)
    raise PypeProtocolError(f"unexpected status {status}: {detail}")


def _describe(resp: requests.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return f"{resp.status_code} {body.get('detail', body)}"
        return f"{resp.status_code} {body}"
    except (ValueError, requests.JSONDecodeError):
        text = resp.text
        return f"{resp.status_code} {text[:200]}" if text else f"HTTP {resp.status_code}"


def call(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout_seconds: float | None,
    **kwargs: Any,  # noqa: ANN401  passthrough to requests.Session.request
) -> requests.Response:
    """Single chokepoint for HTTP calls. Wraps `requests` exceptions in PypeNetworkError."""
    try:
        return session.request(method, url, timeout=timeout_seconds, **kwargs)
    except requests.exceptions.Timeout as exc:
        # Local socket timeout — the server didn't respond within our deadline. Surface as a
        # PypeTimeoutError so callers can catch the same class for both server-503 and local
        # exhaustion.
        raise PypeTimeoutError(f"local HTTP timeout calling {method} {url}") from exc
    except requests.exceptions.RequestException as exc:
        raise PypeNetworkError(f"network error calling {method} {url}: {exc}") from exc
