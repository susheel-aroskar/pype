import ipaddress
import socket
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# RFC 1918 private address blocks. We deliberately exclude link-local (169.254/16),
# carrier-grade NAT (100.64/10), and loopback (127/8) — those are not what we want
# advertised as "the internal IP this server is reachable at".
_RFC_1918_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)


@lru_cache(maxsize=1)
def detect_internal_ip() -> str:
    """Best-effort discovery of this host's RFC 1918 private IPv4 address.

    Strategy:
    1. Resolve the hostname to all its addresses via `gethostbyname_ex`.
    2. Also open a UDP socket "to" 8.8.8.8 (no packet sent) and read what the OS
       picks as the local source address — useful on machines whose hostname
       only resolves to loopback (common in containers, dev VMs).
    3. From the collected candidates, return the first that falls in 10.0.0.0/8,
       172.16.0.0/12, or 192.168.0.0/16. Otherwise return "127.0.0.1".

    The result is cached for the lifetime of the process — the host's primary
    private IP rarely changes mid-run, and we use this only to populate one
    response header on auth, so re-detection is wasteful.

    Used to populate the `X-Pype-Server-IP` header on auth responses, which clients
    and services then echo back on every subsequent request. This lets a future
    reverse proxy / load balancer in front of pype implement sticky routing
    (always send a given client's traffic back to the same pype instance, which
    holds that client's queue in memory).
    """
    candidates: list[str] = []
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 53))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass

    for candidate in candidates:
        try:
            address = ipaddress.IPv4Address(candidate)
        except (ipaddress.AddressValueError, ValueError):
            continue
        if any(address in network for network in _RFC_1918_NETWORKS):
            return candidate
    return "127.0.0.1"


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
    client_reaper_batch_size: int = Field(
        default=1000,
        ge=1,
        description=(
            "MINIMUM number of registry entries the reaper examines per tick. The actual "
            "per-tick batch scales up with registry size to keep full-sweep latency "
            "bounded by `client_reaper_target_sweep_seconds` (see below). Small "
            "registries always do at least this many per tick so they can be swept in "
            "one go without artificial throttling."
        ),
    )
    client_reaper_target_sweep_seconds: float = Field(
        default=600.0,
        gt=0,
        description=(
            "Target wall-clock time for the reaper to complete a full sweep of the "
            "client registry, regardless of size. The reaper computes its per-tick "
            "batch as `max(client_reaper_batch_size, ceil(registry_size / "
            "(target_sweep_seconds / period_seconds)))`. Default 600s = 10 min, which "
            "matches the default inactivity threshold so stale entries are caught "
            "within roughly one threshold window."
        ),
    )
    client_inactivity_threshold_seconds: float = Field(default=600.0, gt=0)

    default_content_type: str = Field(default="application/json")

    internal_ip: str = Field(
        default_factory=detect_internal_ip,
        description=(
            "This server's RFC 1918 private IPv4 address. Returned to callers in the "
            "`X-Pype-Server-IP` header on auth responses; clients echo it back on every "
            "subsequent request to enable sticky routing if pype runs behind a reverse "
            "proxy / load balancer. Auto-detected at startup; override via env if needed."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
