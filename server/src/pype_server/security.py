import secrets
from typing import Annotated, Any, Literal, TypedDict

import jwt
from fastapi import Depends, Header
from fastapi.security.utils import get_authorization_scheme_param

from pype_server.config import Settings, get_settings
from pype_server.exceptions import AuthError, RoleMismatchError


class ServiceClaims(TypedDict):
    role: Literal["service"]
    name: str
    service_secret: str


class ClientClaims(TypedDict):
    role: Literal["client"]
    name: str
    client_id: int
    client_secret: str


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def issue_service_jwt(name: str, service_secret: str, settings: Settings) -> str:
    payload: dict[str, Any] = {"role": "service", "name": name, "service_secret": service_secret}
    return jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def issue_client_jwt(name: str, client_id: int, client_secret: str, settings: Settings) -> str:
    payload: dict[str, Any] = {
        "role": "client",
        "name": name,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    return jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def _extract_and_decode(authorization: str | None, settings: Settings) -> dict[str, Any]:
    """Pull bearer token from header and decode it. Plain helper, not a FastAPI dep."""
    if not authorization:
        raise AuthError("missing Authorization header")
    scheme, token = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Authorization header must be 'Bearer <token>'")
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or expired token") from exc


def service_claims(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> ServiceClaims:
    claims = _extract_and_decode(authorization, settings)
    if claims.get("role") != "service":
        raise RoleMismatchError("expected role=service")
    name = claims.get("name")
    service_secret = claims.get("service_secret")
    if not isinstance(name, str) or not isinstance(service_secret, str):
        raise AuthError("malformed service token")
    return ServiceClaims(role="service", name=name, service_secret=service_secret)


def client_claims(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> ClientClaims:
    claims = _extract_and_decode(authorization, settings)
    if claims.get("role") != "client":
        raise RoleMismatchError("expected role=client")
    name = claims.get("name")
    client_id = claims.get("client_id")
    client_secret = claims.get("client_secret")
    if (
        not isinstance(name, str)
        # `bool` is a subclass of `int` in Python, so `isinstance(True, int)` is True.
        # A JWT carrying `client_id: true` would otherwise pass the int check below;
        # explicitly rejecting bools keeps id semantics tight.
        or not isinstance(client_id, int)
        or isinstance(client_id, bool)
        or not isinstance(client_secret, str)
    ):
        raise AuthError("malformed client token")
    return ClientClaims(role="client", name=name, client_id=client_id, client_secret=client_secret)
