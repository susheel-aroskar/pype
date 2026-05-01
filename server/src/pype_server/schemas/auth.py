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
    client_id: str = Field(
        ...,
        description=(
            "Server-generated 256-bit random URL-safe identifier. Doubles as the client's "
            "capability — knowing the id is what proves authority over this client's queue."
        ),
    )


class ClientLogoffResponse(BaseModel):
    client_id: str
    status: Literal["logged_off"] = "logged_off"
