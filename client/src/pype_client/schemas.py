from pydantic import BaseModel, Field


class ServiceAuthRequest(BaseModel):
    """Body sent to `POST /auth/service`. Wrapper kept thin for future extensibility."""

    service_name: str = Field(..., min_length=1)


class ClientAuthRequest(BaseModel):
    """Body sent to `POST /auth/client`. Wrapper kept thin for future extensibility."""

    name: str = Field(..., min_length=1)
