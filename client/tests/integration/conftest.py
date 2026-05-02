"""Integration-test fixtures: spawn a real pype server in a subprocess.

Why subprocess (not in-process or asyncio): the client uses synchronous `requests`, the
server is async FastAPI. Spinning uvicorn in a subprocess gives us true network I/O so the
client exercises its real code path. The fixture is session-scoped so we pay the startup
cost once per test session.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
import requests


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_server(base_url: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{base_url}/load/services", timeout=0.5)
            if r.status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"pype server did not become ready at {base_url} within {timeout_s}s")


@pytest.fixture(scope="session")
def server_base_url() -> Iterator[str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    # Tighten test settings: smaller queues, smaller server-max timeout, low-latency reaper off.
    env.update(
        {
            "PYPE_SERVER_MAX_TIMEOUT_MS": "5000",
            "PYPE_SERVER_CLIENT_QUEUE_MAX_SIZE": "4",
            "PYPE_SERVER_SERVICE_QUEUE_MAX_SIZE": "8",
            "PYPE_SERVER_CLIENT_REAPER_PERIOD_SECONDS": "3600",
            "PYPE_SERVER_CLIENT_INACTIVITY_THRESHOLD_SECONDS": "3600",
        }
    )
    # Don't capture server stdout/stderr in this fixture: let uvicorn's output flow
    # through to the parent process. With `--log-level warning`, the success case
    # stays quiet, but a server-side traceback (e.g., on an unexpected 500) becomes
    # visible — pytest will buffer it and surface it on test failure rather than
    # silently swallowing it into an unread PIPE.
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pype_server.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
    )
    try:
        _wait_for_server(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
