import jwt
import pytest
from pype_server.config import Settings
from pype_server.exceptions import AuthError, RoleMismatchError
from pype_server.security import (
    client_claims,
    generate_secret,
    issue_client_jwt,
    issue_service_jwt,
    service_claims,
)


def _settings() -> Settings:
    return Settings()


def test_generate_secret_is_unique_and_long_enough() -> None:
    a, b = generate_secret(), generate_secret()
    assert a != b
    assert len(a) >= 32


def test_issue_client_jwt_round_trip() -> None:
    s = _settings()
    token = issue_client_jwt("alice", "cid-abc", s)
    decoded = jwt.decode(token, s.jwt_secret_key.get_secret_value(), algorithms=[s.jwt_algorithm])
    assert decoded == {"role": "client", "name": "alice", "client_id": "cid-abc"}


def test_issue_service_jwt_round_trip() -> None:
    s = _settings()
    token = issue_service_jwt("svc", "sec", s)
    decoded = jwt.decode(token, s.jwt_secret_key.get_secret_value(), algorithms=[s.jwt_algorithm])
    assert decoded == {"role": "service", "name": "svc", "service_secret": "sec"}


def test_client_claims_accepts_valid_client_token() -> None:
    s = _settings()
    token = issue_client_jwt("alice", "cid-abc", s)
    claims = client_claims(s, f"Bearer {token}")
    assert claims == {"role": "client", "name": "alice", "client_id": "cid-abc"}


def test_service_claims_accepts_valid_service_token() -> None:
    s = _settings()
    token = issue_service_jwt("svc", "sec", s)
    claims = service_claims(s, f"Bearer {token}")
    assert claims == {"role": "service", "name": "svc", "service_secret": "sec"}


def test_client_claims_rejects_missing_header() -> None:
    s = _settings()
    with pytest.raises(AuthError):
        client_claims(s, None)


def test_client_claims_rejects_wrong_scheme() -> None:
    s = _settings()
    with pytest.raises(AuthError):
        client_claims(s, "Basic abc")


def test_client_claims_rejects_garbage_token() -> None:
    s = _settings()
    with pytest.raises(AuthError):
        client_claims(s, "Bearer not-a-jwt")


def test_client_claims_rejects_service_token() -> None:
    s = _settings()
    token = issue_service_jwt("svc", "sec", s)
    with pytest.raises(RoleMismatchError):
        client_claims(s, f"Bearer {token}")


def test_service_claims_rejects_client_token() -> None:
    s = _settings()
    token = issue_client_jwt("alice", "cid-abc", s)
    with pytest.raises(RoleMismatchError):
        service_claims(s, f"Bearer {token}")


def test_client_claims_rejects_token_signed_with_wrong_key() -> None:
    s = _settings()
    bad_token = jwt.encode(
        {"role": "client", "name": "alice", "client_id": "cid-abc"},
        "different-secret",
        algorithm=s.jwt_algorithm,
    )
    with pytest.raises(AuthError):
        client_claims(s, f"Bearer {bad_token}")


def test_client_claims_rejects_non_string_client_id() -> None:
    """Numeric client_ids in tokens should be rejected — client_id is always a string."""
    s = _settings()
    bad_token = jwt.encode(
        {"role": "client", "name": "alice", "client_id": 42},
        s.jwt_secret_key.get_secret_value(),
        algorithm=s.jwt_algorithm,
    )
    with pytest.raises(AuthError):
        client_claims(s, f"Bearer {bad_token}")
