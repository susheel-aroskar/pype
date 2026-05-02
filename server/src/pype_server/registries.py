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

    The `client_id` is a 256-bit random URL-safe string signed into the client's JWT.
    The JWT signature is the sole authority — knowing the id (and a server-signed JWT
    that contains it) is what proves authority over the queue. There is no separate
    secret. Entries are created lazily by `get_or_create` on first use of the
    `/clients/{client_id}` endpoint (either a service POSTing a response, or the
    client itself GETting); `POST /auth/client` does NOT pre-register an entry.

    Lazy creation is deliberately friendly to a clustered deployment: a client whose
    JWT lands on a previously-unseen Pype instance just gets a fresh queue there.
    The reaper sweeps abandoned queues by `last_seen` threshold.
    """

    def __init__(self, queue_max_size: int) -> None:
        self._queue_max_size = queue_max_size
        self._entries: dict[str, ClientEntry] = {}

    def get_or_create(self, client_id: str) -> ClientEntry:
        """Return the entry for `client_id`, creating one with an empty queue if missing.

        Single-threaded asyncio means no lock is needed: between the dict lookup and
        the dict insert there are no `await` points, so no other coroutine can race
        in to insert the same key. Collisions among 256-bit random ids are
        cryptographically unreachable (~2^-196 even at one billion concurrent clients).
        """
        existing = self._entries.get(client_id)
        if existing is not None:
            return existing
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
