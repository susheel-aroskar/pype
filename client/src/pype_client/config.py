from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PYPE_CLIENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL of the pype server (scheme + host + port, no trailing slash).",
    )
    network_buffer_ms: int = Field(
        default=1000,
        ge=0,
        description=(
            "Extra ms added to the requests-library HTTP timeout on top of the user-supplied "
            "per-call timeout, to allow the server time to return 204/503 cleanly."
        ),
    )
    auth_timeout_ms: int = Field(
        default=5000,
        ge=1,
        description=(
            "HTTP timeout (ms) used for the auth endpoints (POST /auth/service, "
            "POST /auth/client, DELETE /auth/client). These calls are non-blocking on the "
            "server, so we use this value directly without adding network_buffer_ms."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
