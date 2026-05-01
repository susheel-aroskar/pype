import pytest
import responses
from pydantic import ValidationError
from pype_client import (
    ClientAuthRequest,
    PypeAuthError,
    PypeClient,
    PypeProtocolError,
    ServiceAuthRequest,
    ServiceClient,
)
from pype_client.config import Settings


def _settings() -> Settings:
    return Settings(base_url="http://test.local", network_buffer_ms=100)


@responses.activate
def test_service_authenticate_returns_connection() -> None:
    responses.post(
        "http://test.local/auth/service",
        json={
            "access_token": "fake.jwt.token",
            "token_type": "Bearer",
            "role": "service",
            "name": "billing",
            "service_secret": "ssss",
        },
        status=200,
    )
    sc = ServiceClient(settings=_settings())
    conn = sc.authenticate(ServiceAuthRequest(service_name="billing"))
    assert conn.service_name == "billing"
    assert conn.access_token == "fake.jwt.token"
    # Authorization header gets attached on the session.
    assert conn._session.headers["Authorization"] == "Bearer fake.jwt.token"


@responses.activate
def test_service_authenticate_propagates_401() -> None:
    responses.post(
        "http://test.local/auth/service",
        json={"detail": "nope", "code": "UNAUTHENTICATED"},
        status=401,
    )
    sc = ServiceClient(settings=_settings())
    with pytest.raises(PypeAuthError):
        sc.authenticate(ServiceAuthRequest(service_name="billing"))


@responses.activate
def test_service_authenticate_rejects_malformed_response() -> None:
    responses.post(
        "http://test.local/auth/service",
        json={"unexpected": "shape"},
        status=200,
    )
    sc = ServiceClient(settings=_settings())
    with pytest.raises(PypeProtocolError):
        sc.authenticate(ServiceAuthRequest(service_name="billing"))


@responses.activate
def test_pype_client_authenticate_returns_connection() -> None:
    responses.post(
        "http://test.local/auth/client",
        json={
            "access_token": "client.jwt",
            "token_type": "Bearer",
            "role": "client",
            "name": "alice",
            "client_id": "cid-abc",
        },
        status=200,
    )
    pc = PypeClient(settings=_settings())
    conn = pc.authenticate(ClientAuthRequest(name="alice"))
    assert conn.client_id == "cid-abc"
    assert conn.name == "alice"


def test_service_auth_request_rejects_empty_service_name() -> None:
    # The service_name validation lives on the schema now, not on ServiceClient.__init__.
    with pytest.raises(ValidationError):
        ServiceAuthRequest(service_name="")
