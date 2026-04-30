import asyncio
import time
from dataclasses import dataclass, field
from itertools import count

from pype_server.messaging import PypeRequest, PypeResponse


@dataclass(slots=True)
class ClientEntry:
    client_id: int
    client_secret: str
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
    def __init__(self, queue_max_size: int) -> None:
        self._queue_max_size = queue_max_size
        self._entries: dict[int, ClientEntry] = {}
        self._id_seq = count(1)

    def register(self, client_secret: str) -> ClientEntry:
        client_id = next(self._id_seq)
        queue: asyncio.Queue[PypeResponse] = asyncio.Queue(maxsize=self._queue_max_size)
        entry = ClientEntry(client_id=client_id, client_secret=client_secret, queue=queue)
        self._entries[client_id] = entry
        return entry

    def get(self, client_id: int) -> ClientEntry | None:
        return self._entries.get(client_id)

    def remove(self, client_id: int) -> ClientEntry | None:
        return self._entries.pop(client_id, None)

    def bump_last_seen(self, client_id: int) -> None:
        entry = self._entries.get(client_id)
        if entry is not None:
            entry.last_seen = time.monotonic()

    def snapshot_keys(self) -> list[int]:
        return list(self._entries.keys())

    def __len__(self) -> int:
        return len(self._entries)
