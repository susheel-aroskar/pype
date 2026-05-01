import asyncio
import time
from dataclasses import dataclass, field

from pype_server.messaging import PypeRequest, PypeResponse


@dataclass(slots=True)
class ClientEntry:
    client_id: str
    queue: asyncio.Queue[PypeResponse]
    last_seen: float = field(default_factory=time.monotonic)


class ServiceRegistry:
    def __init__(self, queue_max_size: int) -> None:
        self._queue_max_size = queue_max_size
        self._queues: dict[str, asyncio.Queue[PypeRequest]] = {}

    def get_or_create(self, service_name: str) -> asyncio.Queue[PypeRequest]:
        existing = self._queues.get(service_name)
        if existing is not None:
            return existing
        queue: asyncio.Queue[PypeRequest] = asyncio.Queue(maxsize=self._queue_max_size)
        self._queues[service_name] = queue
        return queue

    def snapshot_loads(self) -> list[tuple[str, int]]:
        return [(name, q.qsize()) for name, q in self._queues.items()]


class ClientRegistry:
    """Holds one in-memory `ClientEntry` per active client, keyed by `client_id`.

    The `client_id` is a 256-bit random URL-safe string that doubles as the client's
    capability: knowing the id is what proves authority over the client's queue. There is
    no separate `client_secret` in this design — the id is unguessable, so we only need
    to check that an entry with that id exists in the registry.
    """

    def __init__(self, queue_max_size: int) -> None:
        self._queue_max_size = queue_max_size
        self._entries: dict[str, ClientEntry] = {}

    def register(self, client_id: str) -> ClientEntry:
        """Register a new client and return its entry.

        The caller is responsible for supplying a `client_id` that is not already in the
        registry. In this codebase the only caller is `POST /auth/client`, which uses
        `secrets.token_urlsafe(32)` — 256 bits of entropy. Collisions among live clients
        are cryptographically unreachable (~2^-196 even at one billion concurrent clients),
        so we do not check.
        """
        queue: asyncio.Queue[PypeResponse] = asyncio.Queue(maxsize=self._queue_max_size)
        entry = ClientEntry(client_id=client_id, queue=queue)
        self._entries[client_id] = entry
        return entry

    def get(self, client_id: str) -> ClientEntry | None:
        return self._entries.get(client_id)

    def remove(self, client_id: str) -> ClientEntry | None:
        return self._entries.pop(client_id, None)

    def bump_last_seen(self, client_id: str) -> None:
        entry = self._entries.get(client_id)
        if entry is not None:
            entry.last_seen = time.monotonic()

    def snapshot_keys(self) -> list[str]:
        return list(self._entries.keys())

    def __len__(self) -> int:
        return len(self._entries)
