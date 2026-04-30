from typing import Literal

from pydantic import BaseModel, Field


class ServiceAuthRequest(BaseModel):
    service_name: str = Field(..., min_length=1, description="Name of the backend service.")


class ClientAuthRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Caller-provided name for the client.")


class ServiceAuthResponse(BaseModel):
    access_token: str = Field(..., description="JWT to be sent as 'Authorization: Bearer ...'.")
    token_type: Literal["Bearer"] = "Bearer"
    role: Literal["service"] = "service"
    name: str
    service_secret: str = Field(
        ..., description="Server-generated secret. For future use; not validated yet."
    )


class ClientAuthResponse(BaseModel):
    access_token: str = Field(..., description="JWT to be sent as 'Authorization: Bearer ...'.")
    token_type: Literal["Bearer"] = "Bearer"
    role: Literal["client"] = "client"
    name: str
    client_id: int
    client_secret: str


class ClientLogoffResponse(BaseModel):
    client_id: int
    status: Literal["logged_off"] = "logged_off"
