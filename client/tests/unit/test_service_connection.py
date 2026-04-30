import pytest
import responses
from pype_client import (
    PypeBadRequestError,
    PypeClientGoneError,
    PypeForbiddenError,
    PypeProtocolError,
    PypeTimeoutError,
    ServiceAuthRequest,
    ServiceClient,
)
from pype_client.config import Settings


def _settings() -> Settings:
    return Settings(base_url="http://test.local", network_buffer_ms=100)


def _stub_service_auth() -> None:
    responses.post(
        "http://test.local/auth/service",
        json={
            "access_token": "jwt",
            "token_type": "Bearer",
            "role": "service",
            "name": "billing",
            "service_secret": "s",
        },
        status=200,
    )


@responses.activate
def test_get_request_returns_client_request_with_headers() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing",
        body=b'{"amount": 5}',
        status=200,
        headers={
            "X-Pype-Client-Id": "42",
            "X-Pype-Client-Secret": "cs",
            "X-Pype-Request-Id": "r1",
            "Content-Type": "application/json",
        },
    )
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    req = conn.get_request(timeout=0)
    assert req.client_id == 42
    assert req.client_secret == "cs"
    assert req.request_id == "r1"
    assert req.content_type == "application/json"
    assert req.bytes == b'{"amount": 5}'
    assert req.json() == {"amount": 5}
    assert req.service_name == "billing"


@responses.activate
def test_get_request_raises_timeout_on_204() -> None:
    _stub_service_auth()
    responses.get("http://test.local/services/billing", status=204)
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    with pytest.raises(PypeTimeoutError):
        conn.get_request(timeout=0)


@responses.activate
def test_get_request_propagates_400() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing", json={"detail": "bad", "code": "X"}, status=400
    )
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    with pytest.raises(PypeBadRequestError):
        conn.get_request(timeout=0)


@responses.activate
def test_get_request_raises_protocol_error_on_missing_headers() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing",
        body=b"x",
        status=200,
        headers={"Content-Type": "text/plain"},  # no X-Pype-* headers
    )
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    with pytest.raises(PypeProtocolError):
        conn.get_request(timeout=0)


@responses.activate
def test_send_response_posts_with_query_params_and_body() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing",
        body=b'{"x": 1}',
        status=200,
        headers={
            "X-Pype-Client-Id": "5",
            "X-Pype-Client-Secret": "cs",
            "X-Pype-Request-Id": "rid",
            "Content-Type": "application/json",
        },
    )
    responses.post("http://test.local/clients/5", status=202)
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    req = conn.get_request(timeout=0)
    req.send_response(b"OK", content_type="text/plain", timeout=0)

    posted = next(
        c for c in responses.calls if c.request.method == "POST" and "/clients/5" in c.request.url
    )
    assert b"OK" == posted.request.body
    assert "client_secret=cs" in posted.request.url
    assert "request_id=rid" in posted.request.url
    assert "timeout=0" in posted.request.url
    assert posted.request.headers["Content-Type"] == "text/plain"


@responses.activate
def test_send_response_str_payload_is_utf8_encoded() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing",
        body=b"{}",
        status=200,
        headers={
            "X-Pype-Client-Id": "1",
            "X-Pype-Client-Secret": "cs",
            "X-Pype-Request-Id": "r",
            "Content-Type": "application/json",
        },
    )
    responses.post("http://test.local/clients/1", status=202)
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    req = conn.get_request(timeout=0)
    req.send_response("héllo")  # str → utf-8

    posted = next(
        c for c in responses.calls if c.request.method == "POST" and "/clients/" in c.request.url
    )
    assert posted.request.body == "héllo".encode()


@responses.activate
def test_send_response_propagates_410_when_client_gone() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing",
        body=b"{}",
        status=200,
        headers={
            "X-Pype-Client-Id": "9",
            "X-Pype-Client-Secret": "cs",
            "X-Pype-Request-Id": "r",
            "Content-Type": "application/json",
        },
    )
    responses.post("http://test.local/clients/9", json={"detail": "gone", "code": "X"}, status=410)
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    req = conn.get_request(timeout=0)
    with pytest.raises(PypeClientGoneError):
        req.send_response(b"x")


@responses.activate
def test_send_response_propagates_403() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing",
        body=b"{}",
        status=200,
        headers={
            "X-Pype-Client-Id": "1",
            "X-Pype-Client-Secret": "cs",
            "X-Pype-Request-Id": "r",
            "Content-Type": "application/json",
        },
    )
    responses.post(
        "http://test.local/clients/1", json={"detail": "no", "code": "FORBIDDEN"}, status=403
    )
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    req = conn.get_request(timeout=0)
    with pytest.raises(PypeForbiddenError):
        req.send_response(b"x")


@responses.activate
def test_send_response_propagates_503_as_timeout() -> None:
    _stub_service_auth()
    responses.get(
        "http://test.local/services/billing",
        body=b"{}",
        status=200,
        headers={
            "X-Pype-Client-Id": "1",
            "X-Pype-Client-Secret": "cs",
            "X-Pype-Request-Id": "r",
            "Content-Type": "application/json",
        },
    )
    responses.post("http://test.local/clients/1", json={"detail": "full", "code": "X"}, status=503)
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    req = conn.get_request(timeout=0)
    with pytest.raises(PypeTimeoutError):
        req.send_response(b"x")


@responses.activate
def test_close_makes_further_calls_raise() -> None:
    _stub_service_auth()
    conn = ServiceClient(settings=_settings()).authenticate(
        ServiceAuthRequest(service_name="billing")
    )
    conn.close()
    with pytest.raises(RuntimeError):
        conn.get_request(timeout=0)


@responses.activate
def test_context_manager_closes_on_exit() -> None:
    _stub_service_auth()
    sc = ServiceClient(settings=_settings())
    with sc.authenticate(ServiceAuthRequest(service_name="billing")) as conn:
        assert conn._closed is False
    assert conn._closed is True
