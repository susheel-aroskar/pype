from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PYPE_SERVER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_secret_key: SecretStr = Field(
        default=SecretStr("dev-only-secret-change-in-production"),
        description=(
            "HMAC key used to sign JWTs. Override via PYPE_SERVER_JWT_SECRET_KEY in production."
        ),
    )
    jwt_algorithm: str = Field(default="HS256")

    max_timeout_ms: int = Field(default=480_000, ge=1)
    client_queue_max_size: int = Field(default=8, ge=1)
    service_queue_max_size: int = Field(default=8000, ge=1)

    client_reaper_period_seconds: float = Field(default=60.0, gt=0)
    client_reaper_batch_size: int = Field(default=1000, ge=1)
    client_inactivity_threshold_seconds: float = Field(default=600.0, gt=0)

    default_content_type: str = Field(default="application/json")


@lru_cache
def get_settings() -> Settings:
    return Settings()
