from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PypeRequest:
    client_id: int
    client_secret: str
    request_id: str
    content_type: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class PypeResponse:
    request_id: str
    content_type: str
    payload: bytes
