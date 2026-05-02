"""
================================================================================
  PYPE DEMO — A guided tour of pype-server and pype-client.
================================================================================

This file is a self-contained demonstration of every capability of the pype
rendezvous platform. It is also a working pytest integration test, so a green
'PASSED' at the end is real proof that the whole system works end-to-end.

HOW TO RUN
----------

    pytest client/tests/integration/test_demo.py -s -v

The `-s` flag is essential. Without it, pytest captures stdout and the
narrated transcript this demo prints is hidden.

WHAT THIS DEMO SHOWS
--------------------

1. Three different backend services authenticating with pype, each running in
   its own thread, each implemented as its own class:
     - BinaryEchoService:   echoes raw bytes back, demonstrating end-to-end
                            binary payload preservation.
     - DoublerService:      reads a JSON int and returns its double, the
                            classic JSON-RPC-style example.
     - DiceRollService:     rolls a 6-sided die after a small randomized
                            delay, used to make response ordering nontrivial.

2. Three different callers (clients) using the pype-client library, each in
   its own thread, each implemented as its own class. Each one focuses on a
   different capability of the client API:
     - SequentialCaller (Alice)  -> ClientConnection.get_response()
     - ParallelCaller (Bob)      -> get_any_responses(),  get_all_responses()
     - QuorumCaller (Carol)      -> get_response_quorum()

3. Real concurrency: all six actors (3 services + 3 clients) run in their own
   OS threads. The pype server itself is a real FastAPI app spawned in a
   subprocess by the existing `server_base_url` fixture. There is no mocking.

WHAT TO EXPECT IN THE TRANSCRIPT
--------------------------------

Each line of console output is prefixed with the actor that produced it,
e.g. `[ ALICE  ]` for the Alice client, `[ DICE   ]` for the dice service.
Service messages are indented to the right and colored warm (yellow / magenta /
red); client messages are flush left and colored cool (cyan / green / blue).
This makes it easy to follow who said what when threads are interleaving.

A small sleep between actions paces the output for human reading; the whole
demo finishes in roughly two to four seconds.

================================================================================
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections import Counter
from typing import ClassVar

from pype_client import (
    ClientAuthRequest,
    ClientRequest,
    PypeClient,
    PypeClientGoneError,
    PypeTimeoutError,
    ServiceAuthRequest,
    ServiceClient,
)
from pype_client.config import Settings


# ANSI escape sequences for terminal colors. Defined up here because services and
# callers reference these as class-attribute defaults (e.g. `DISPLAY_COLOR = Ansi.GREEN`),
# which are evaluated at class-definition time — so `Ansi` must already exist by then.
# The thread-safe print helpers further down can stay there because they're used from
# method bodies (evaluated at call time, not class-definition time).
class Ansi:
    """ANSI escape sequences for terminal colors. Any modern terminal supports these."""

    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"

    # Cool colors -> clients
    CYAN = "\x1b[36m"
    GREEN = "\x1b[32m"
    BLUE = "\x1b[34m"

    # Warm colors -> services
    YELLOW = "\x1b[33m"
    MAGENTA = "\x1b[35m"
    RED = "\x1b[31m"

    # Neutrals
    GREY = "\x1b[37m"
    BRIGHT_WHITE = "\x1b[97m"


# ============================================================================
# Backend services — each one demonstrates a different request/response shape.
# ============================================================================
#
# THE BIG IDEA — PULL, DON'T LISTEN
# ----------------------------------
# A pype-backed service is NOT an HTTP server. It does not bind a port, does
# not sit behind a load balancer, and is never directly addressable from
# clients. Instead, it is itself an HTTP *client* of the pype server, and it
# *pulls* work from a queue named after its `service_name`. The shape of one
# work cycle is:
#
#     1.  Authenticate once via `ServiceClient.authenticate(...)` — gets a
#         JWT bound to the service_name. The pype server creates (or reuses)
#         a queue for this service_name in its `service_registry`.
#
#     2.  Loop forever (or until stop):
#
#           a. `request = connection.get_request(timeout=...)`
#                  Sends a long-poll GET to /services/{service_name} on pype.
#                  Pype either dequeues a PypeRequest and returns it, or
#                  returns 204 if the queue stayed empty for the entire
#                  timeout window. The 204 case bubbles up here as a
#                  PypeTimeoutError, which we catch and use to re-check the
#                  stop flag, keeping shutdown latency bounded.
#
#                  Crucially, the `request` object carries with it the
#                  identity of the originating client (`client_id`,
#                  `request_id`) — these were stamped onto it by the pype
#                  server when it dequeued the request. The service doesn't
#                  have to know "who called"; it just sees the request and
#                  reads the headers if it needs them.
#
#           b. (do work — JSON-decode, compute, whatever)
#
#           c. `request.send_response(payload, content_type, ...)`
#                  POSTs back to /clients/{request.client_id} on pype with
#                  the response payload. The library uses the `client_id`
#                  and `request_id` already captured on the ClientRequest
#                  object — the service code never has to manage routing
#                  addresses, JWTs of the original caller, or any other
#                  "where do I send this back" plumbing. Pype enqueues the
#                  response on the originating client's per-client queue,
#                  where it waits for the client to pull it.
#
# WHY THIS IS NICE
# ----------------
# - Service instances scale by ADDING more workers that authenticate with
#   the same service_name and pull from the same queue. No load balancer,
#   no service-discovery registry, no explicit routing.
# - Failure isolation is automatic: if a service worker dies mid-request,
#   the request stays on the server's queue (or, if it was already
#   dequeued, it's lost — and the client times out and retries).
# - Backpressure is automatic too: when service workers can't keep up,
#   the queue fills up, and `POST /services/{name}` from clients starts
#   returning 503 → `PypeTimeoutError`. No flow-control protocol needed.
#
# We factor the lifecycle into a small `BackendService` base class so the
# per-service classes can stay focused on the only thing that's specific to
# them: how to handle one request. This is the canonical shape of a
# pype-backed service.


class BackendService:
    """Base for services in this demo. Subclasses override `process_one_request`.

    The lifecycle is:
      1. start()   spawns a worker thread.
      2. The thread authenticates with pype, then loops:
            request = connection.get_request(timeout=...)
            (do work)
            request.send_response(...)
      3. stop()    signals the loop to exit and joins the thread.
    """

    SERVICE_NAME: ClassVar[str] = ""
    DISPLAY_TAG: ClassVar[str] = ""
    DISPLAY_COLOR: ClassVar[str] = ""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_safely, name=self.SERVICE_NAME, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    # ------------------------------------------------------------------ private

    def _log(self, message: str) -> None:
        log_service(self.DISPLAY_TAG, self.DISPLAY_COLOR, message)

    def _run_safely(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._error = exc
            self._log(f"ERROR: {exc!r}")

    def _run(self) -> None:
        """Authenticate, then drain requests until stop is signaled.

        Note the polling-style stop pattern: we ask the server for a request
        with a short timeout, and if no request arrives within that window we
        check the stop_event and either loop again or exit. This keeps the
        shutdown latency bounded.
        """
        client_settings = Settings(base_url=self.base_url, network_buffer_ms=2000)
        service_client = ServiceClient(settings=client_settings)
        with service_client.authenticate(
            ServiceAuthRequest(service_name=self.SERVICE_NAME)
        ) as connection:
            self._log(f"authenticated as service {self.SERVICE_NAME!r}; ready for requests")
            while not self._stop_event.is_set():
                try:
                    # Short server-side timeout so we can react to stop_event regularly.
                    client_request = connection.get_request(timeout=200)
                except PypeTimeoutError:
                    continue
                try:
                    self.process_one_request(client_request)
                except PypeClientGoneError:
                    # The originating client closed before we could send the response.
                    # This is a normal pype scenario (e.g., quorum reached -> client moved
                    # on); the response is dropped on the floor and we continue serving.
                    self._log(
                        f"client {client_request.client_id} closed before response sent — drop"
                    )
            self._log("shutting down")

    def process_one_request(self, client_request: ClientRequest) -> None:
        """Override in a subclass. The default is a programming error."""
        raise NotImplementedError


class BinaryEchoService(BackendService):
    """Receives an arbitrary byte payload and echoes it back unchanged.

    Demonstrates that pype is fully content-agnostic: any bytes a client
    POSTs survive the round-trip with their `Content-Type` header preserved.
    """

    SERVICE_NAME = "echo"
    DISPLAY_TAG = "  ECHO  "
    DISPLAY_COLOR = Ansi.YELLOW

    def process_one_request(self, client_request: ClientRequest) -> None:
        size_bytes = len(client_request.bytes)
        self._log(
            f"received {size_bytes} bytes (request_id={client_request.request_id}); echoing back"
        )
        # ─── send_response routes back to the originating client automatically ───
        # Notice we don't tell pype "where" to send this response. The
        # `client_request` object already knows: it captured `client_id` and
        # `request_id` from the response headers when it was pulled.
        # `send_response` just POSTs the payload to
        # /clients/{client_request.client_id} on pype, with `request_id` as a
        # query param, and pype enqueues it on that client's per-client queue.
        # ─────────────────────────────────────────────────────────────────────────
        client_request.send_response(
            client_request.bytes, content_type=client_request.content_type
        )


class DoublerService(BackendService):
    """Reads an integer from a JSON request body and returns its double.

    Demonstrates the JSON convenience accessor `.json()` on `ClientRequest`,
    and the `application/json` content type round-trip.
    """

    SERVICE_NAME = "doubler"
    DISPLAY_TAG = "DOUBLER "
    DISPLAY_COLOR = Ansi.MAGENTA

    def process_one_request(self, client_request: ClientRequest) -> None:
        input_number = client_request.json()
        doubled = input_number * 2
        self._log(
            f"received {input_number} (request_id={client_request.request_id}); "
            f"responding {doubled}"
        )
        client_request.send_response(json.dumps(doubled), content_type="application/json")


class DiceRollService(BackendService):
    """Rolls a fair 6-sided die after a small randomized delay.

    The variable delay (50-250 ms) is intentional: it makes response ordering
    nondeterministic, which is what makes `get_any_responses` and
    `get_response_quorum` interesting to demonstrate.
    """

    SERVICE_NAME = "dice"
    DISPLAY_TAG = "  DICE  "
    DISPLAY_COLOR = Ansi.RED

    def process_one_request(self, client_request: ClientRequest) -> None:
        delay_milliseconds = random.randint(50, 250)
        time.sleep(delay_milliseconds / 1000.0)
        roll_result = random.randint(1, 6)
        self._log(
            f"rolled {roll_result} (took {delay_milliseconds}ms, "
            f"request_id={client_request.request_id})"
        )
        client_request.send_response(str(roll_result), content_type="text/plain")


# ============================================================================
# Caller (client-side) demos — one class per pype-client capability.
# ============================================================================
#
# THE BIG IDEA — TWO HALVES OF EVERY CALL, ON THE CALLER'S SCHEDULE
# ------------------------------------------------------------------
# In a traditional HTTP RPC, a single function call blocks the caller until
# the response comes back. Concurrency requires async/await, threads, or
# explicit callbacks — all of which add complexity to caller code.
#
# Pype splits every logical request into two independent halves that the
# caller can interleave however it wants, while staying in plain blocking
# synchronous Python (no `asyncio`, no thread pool):
#
#     ──── HALF 1: SUBMIT ─────────────────────────────────────────────
#       request_id = connection.send_request(service_name, payload, ...)
#
#       This POSTs to /services/{service_name} on pype. Pype enqueues the
#       request on the named service's queue and returns 202 Accepted
#       almost immediately — the call does NOT wait for the backend
#       service to process the request. It returns a client-unique
#       `request_id` (string) you can use later as a handle for the
#       response. You can call `send_request` many times in a row to fan
#       out work; each call returns in milliseconds.
#
#     ──── HALF 2: COLLECT ────────────────────────────────────────────
#       resp                = connection.get_response(request_id, ...)
#       arrived_first       = connection.get_any_responses(*ids, ...)
#       all_results         = connection.get_all_responses(*ids, ...)
#       quorum_results      = connection.get_response_quorum(N, *ids, ...)
#
#       These methods block waiting for the requested response(s), but
#       they don't drive any work themselves — they just pull from this
#       client's per-client queue on pype. Behind the scenes, while you
#       were doing other things, the chosen backend services were pulling
#       your requests, processing it, and POSTing their responses into your
#       queue. Every variant returns ServiceResponse objects you can
#       decode with `.bytes` / `.text` / `.json()`.
#
# WHY THIS IS NICE
# ----------------
# - "Concurrency" comes for free: `send_request` is non-blocking, so a
#   tight loop of N sends fans out N requests across services. The actual
#   parallelism happens on the server side (multiple service workers
#   processing them in parallel) and is invisible to your caller code.
# - The collect API gives you exactly the synchronization primitive your
#   business logic needs — wait for one specific id, wait for the first
#   to arrive, wait for all, wait for a quorum — without you ever writing
#   a Future, a callback, an `await`, or a Thread.
# - Out-of-order responses are handled inside the library: `get_response`
#   never lies to you — if a different request_id's response arrives
#   first, it's cached internally and the loop keeps polling until your
#   id shows up (or the deadline elapses).
#
# Each caller below is fully self-contained; there is intentionally no shared
# base class so a reader can read any one class top-to-bottom and see the
# complete pattern for that capability.


class SequentialCaller:
    """Alice. The simplest possible pype client — one synchronous RPC.

    Uses `ClientConnection.call()`, which sends the request and returns the response
    in a SINGLE HTTP round-trip in the happy path (via the server's `block=1` mode).
    For straightforward "I want my answer back, then I'll move on" call sites, this
    is the recommended shape — it's faster than `send_request` + `get_response` and
    reads like a normal blocking function call.

    For fan-out / scatter-gather / quorum patterns, see Bob and Carol below — they
    use the explicit `send_request` + `get_response*` primitives because keeping
    multiple requests in flight at once is the whole point.
    """

    DISPLAY_TAG = " ALICE  "
    DISPLAY_COLOR = Ansi.CYAN

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.demonstrated_value: int | None = None
        self._error: BaseException | None = None

    def run(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._error = exc
            self._log(f"ERROR: {exc!r}")

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    # ------------------------------------------------------------------ private

    def _log(self, message: str) -> None:
        log_client(self.DISPLAY_TAG, self.DISPLAY_COLOR, message)

    def _run(self) -> None:
        settings = Settings(base_url=self.base_url, network_buffer_ms=2000)
        pype_client = PypeClient(settings=settings)

        # `with` ensures we DELETE /auth/client on exit (best-effort) and close the session.
        with pype_client.authenticate(ClientAuthRequest(name="alice")) as connection:
            self._log("authenticated; doing a one-RTT call to doubler with input 21")
            time.sleep(0.1)

            # ─── pype-client capability spotlight ──────────────────────────────
            #
            #   ClientConnection.call(service_name, payload, content_type=..., timeout=...)
            #
            #   Sends the request and returns its response in ONE HTTP call in the
            #   happy path. Internally, the library uses pype's `block=1` mode on
            #   POST /services/{name}: the server enqueues the request and, in the
            #   same HTTP request, turns around and dequeues this client's response.
            #   If a stale response was at the head of the client's queue (or no
            #   response landed in the time budget), the library transparently
            #   falls back to the standard get_response loop — so the caller's
            #   contract is unchanged: you always get YOUR response or a timeout.
            #
            # ───────────────────────────────────────────────────────────────────
            response = connection.call(
                "doubler", "21", content_type="application/json", timeout=3000
            )
            self.demonstrated_value = response.json()
            self._log(
                f"got back {self.demonstrated_value} (request_id={response.request_id}, "
                f"content-type: {response.content_type})  ✓"
            )


class ParallelCaller:
    """Bob. Fires several requests at once, then drains them efficiently.

    Demonstrates the headline win of pype-client: a single-threaded, blocking
    program can fan out N requests to multiple services, gather whichever
    finishes first, and then collect the rest — all without callbacks,
    threads, or async/await.
    """

    DISPLAY_TAG = "  BOB   "
    DISPLAY_COLOR = Ansi.GREEN

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.first_responses: dict[str, str] = {}
        self.remaining_responses: dict[str, str] = {}
        self._error: BaseException | None = None

    def run(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._error = exc
            self._log(f"ERROR: {exc!r}")

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    # ------------------------------------------------------------------ private

    def _log(self, message: str) -> None:
        log_client(self.DISPLAY_TAG, self.DISPLAY_COLOR, message)

    def _run(self) -> None:
        settings = Settings(base_url=self.base_url, network_buffer_ms=2000)
        pype_client = PypeClient(settings=settings)

        with pype_client.authenticate(ClientAuthRequest(name="bob")) as connection:
            self._log("authenticated; firing 4 requests in parallel across 3 services")
            time.sleep(0.1)

            # ─── note: this is single-threaded sync Python ────────────────────
            # Each `send_request` is a blocking POST that returns 202 Accepted
            # almost immediately (the server enqueues and returns; it does NOT
            # wait for the backend service to actually process the request).
            # So this entire block, despite looking sequential, fans 4 requests
            # out across 3 services in milliseconds. The actual parallelism is
            # happening on the server side: while we move on to call
            # `get_any_responses` below, the doubler / echo / dice services are
            # all processing in parallel.
            # ──────────────────────────────────────────────────────────────────
            doubler_request_id = connection.send_request(
                "doubler", "7", content_type="application/json"
            )
            echo_payload = b"binary blob \x00\x01\x02 across the wire"
            echo_request_id = connection.send_request(
                "echo", echo_payload, content_type="application/octet-stream"
            )
            dice_request_id_1 = connection.send_request("dice", "", content_type="text/plain")
            dice_request_id_2 = connection.send_request("dice", "", content_type="text/plain")

            all_request_ids = (
                doubler_request_id,
                echo_request_id,
                dice_request_id_1,
                dice_request_id_2,
            )
            self._log(f"all 4 enqueued; request_ids = {list(all_request_ids)}")

            # ─── pype-client capability spotlight ──────────────────────────────
            #
            #   ClientConnection.get_any_responses(*request_ids, timeout=...)
            #
            #   Block until ANY of the requested responses is available, then
            #   return EVERY one currently ready (so the caller can drain the
            #   easy wins in a single round-trip). Slower request_ids stay in
            #   the internal cache; future calls — including get_all_responses
            #   below — will pop them when ready.
            #
            # ───────────────────────────────────────────────────────────────────
            self._log("calling get_any_responses() — first arrivals win")
            arrived_first = connection.get_any_responses(*all_request_ids, timeout=3000)
            self.first_responses = {
                rid: r.text if r.content_type.startswith("text") else f"<{len(r.bytes)} bytes>"
                for rid, r in arrived_first.items()
            }
            self._log(
                f"first batch ({len(arrived_first)}/{len(all_request_ids)}): {self.first_responses}"
            )

            # Whatever didn't make the first batch is still in flight.
            remaining_request_ids = tuple(
                rid for rid in all_request_ids if rid not in arrived_first
            )

            if not remaining_request_ids:
                self._log("everything came back in the first wave — nothing to drain")
                return

            # ─── pype-client capability spotlight ──────────────────────────────
            #
            #   ClientConnection.get_all_responses(*request_ids, timeout=...)
            #
            #   Block until ALL of the requested responses are present in the
            #   internal cache, then pop and return them as a dict. Useful for
            #   "scatter / gather": fan N requests out, wait for every one.
            #
            # ───────────────────────────────────────────────────────────────────
            self._log(f"calling get_all_responses() for stragglers: {list(remaining_request_ids)}")
            stragglers = connection.get_all_responses(*remaining_request_ids, timeout=3000)
            self.remaining_responses = {
                rid: r.text if r.content_type.startswith("text") else f"<{len(r.bytes)} bytes>"
                for rid, r in stragglers.items()
            }
            self._log(f"stragglers ({len(stragglers)}): {self.remaining_responses}  ✓")


class QuorumCaller:
    """Carol. Models a real-world distributed-decision pattern: roll several
    dice, accept the first three results as the quorum, and ignore the rest.

    Demonstrates that pype's fan-out-then-fan-in style maps cleanly onto
    classic primitives like Paxos/Raft "ack from majority" without needing
    any new infrastructure.
    """

    DISPLAY_TAG = " CAROL  "
    DISPLAY_COLOR = Ansi.BLUE
    NUMBER_OF_ROLLS = 5
    QUORUM_SIZE = 3

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.quorum_results: dict[str, int] = {}
        self.most_common_face: int | None = None
        self._error: BaseException | None = None

    def run(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._error = exc
            self._log(f"ERROR: {exc!r}")

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    # ------------------------------------------------------------------ private

    def _log(self, message: str) -> None:
        log_client(self.DISPLAY_TAG, self.DISPLAY_COLOR, message)

    def _run(self) -> None:
        settings = Settings(base_url=self.base_url, network_buffer_ms=2000)
        pype_client = PypeClient(settings=settings)

        with pype_client.authenticate(ClientAuthRequest(name="carol")) as connection:
            self._log(
                f"authenticated; rolling {self.NUMBER_OF_ROLLS} dice in parallel, "
                f"will accept first {self.QUORUM_SIZE} as quorum"
            )
            time.sleep(0.1)

            request_ids = tuple(
                connection.send_request("dice", "", content_type="text/plain")
                for _ in range(self.NUMBER_OF_ROLLS)
            )
            self._log(
                f"all {self.NUMBER_OF_ROLLS} dice rolls enqueued; request_ids={list(request_ids)}"
            )

            # ─── pype-client capability spotlight ──────────────────────────────
            #
            #   ClientConnection.get_response_quorum(quorum, *request_ids, timeout=...)
            #
            #   Block until at least `quorum` of `request_ids` are ready, then
            #   return EVERY currently-ready one (size in [quorum, N]). The
            #   stragglers stay cached for later retrieval — but here we don't
            #   need them, so they'll be dropped on close().
            #
            # ───────────────────────────────────────────────────────────────────
            self._log(f"calling get_response_quorum(quorum={self.QUORUM_SIZE}, ...)")
            quorum = connection.get_response_quorum(self.QUORUM_SIZE, *request_ids, timeout=4000)
            self.quorum_results = {rid: int(r.text) for rid, r in quorum.items()}
            face_counts = Counter(self.quorum_results.values())
            self.most_common_face, count = face_counts.most_common(1)[0]
            self._log(
                f"quorum reached with {len(quorum)}/{self.NUMBER_OF_ROLLS} rolls: "
                f"{list(self.quorum_results.values())}"
            )
            self._log(
                f"decision: most-common face is {self.most_common_face} "
                f"(seen {count}x out of {len(quorum)} quorum rolls)  ✓"
            )


# ============================================================================
# The demo test itself — orchestrates everyone above.
# ============================================================================


def test_demo(server_base_url: str) -> None:
    """Spin up 3 services, then run 3 callers concurrently against them."""

    banner("PYPE DEMO  —  starting 3 backend services")

    services: list[BackendService] = [
        BinaryEchoService(server_base_url),
        DoublerService(server_base_url),
        DiceRollService(server_base_url),
    ]
    for service in services:
        service.start()

    # No need to wait for services to be ready before letting clients run!
    # If a client POSTs to /services/{name} before any service has authenticated,
    # pype simply creates that service's queue on the fly and parks the request
    # there. The service authenticates whenever it's ready and finds the work
    # already waiting. Clients and services are decoupled in time as well as in
    # space — that's another small but real win of the rendezvous model.

    try:
        banner("Running 3 callers in parallel  —  Alice, Bob, Carol")

        alice = SequentialCaller(server_base_url)
        bob = ParallelCaller(server_base_url)
        carol = QuorumCaller(server_base_url)

        client_threads = [
            threading.Thread(target=alice.run, name="alice"),
            threading.Thread(target=bob.run, name="bob"),
            threading.Thread(target=carol.run, name="carol"),
        ]
        for thread in client_threads:
            thread.start()
        for thread in client_threads:
            thread.join(timeout=15)

        # Surface any error that happened inside a client thread.
        for caller in (alice, bob, carol):
            caller.raise_if_failed()

        # ─── Light sanity assertions so the demo doubles as a real test ─────
        assert alice.demonstrated_value == 42, "Alice expected 21*2 == 42"
        # Bob's two halves together must cover all four request_ids exactly once.
        all_seen = set(bob.first_responses) | set(bob.remaining_responses)
        assert len(all_seen) == 4
        assert not (set(bob.first_responses) & set(bob.remaining_responses)), (
            "first_responses and remaining_responses must be disjoint"
        )
        assert len(carol.quorum_results) >= QuorumCaller.QUORUM_SIZE
        assert carol.most_common_face in range(1, 7)

        banner("Demo complete  —  all 3 callers succeeded, all 3 services responded.  ✓")
    finally:
        for service in services:
            service.stop()
        for service in services:
            service.raise_if_failed()


# ============================================================================
# Console output helpers — colored, prefixed, thread-safe.
# ============================================================================
#
# Threads can interleave print() calls and produce mangled output. A single
# global lock around every line keeps the transcript readable. The `Ansi`
# class itself lives at the top of the file because services and callers
# reference its values as class-attribute defaults.


_PRINT_LOCK = threading.Lock()


def _say(prefix: str, color: str, message: str, *, indent: int = 0) -> None:
    """Print a single tagged, colored line atomically. `indent` is leading spaces."""
    with _PRINT_LOCK:
        print(f"{' ' * indent}{Ansi.BOLD}{color}[ {prefix} ]{Ansi.RESET}  {message}")


def log_client(prefix: str, color: str, message: str) -> None:
    """Client transcripts are flush-left."""
    _say(prefix, color, message, indent=0)


def log_service(prefix: str, color: str, message: str) -> None:
    """Service transcripts are indented to the right so they read like a swim-lane diagram."""
    _say(prefix, color, message, indent=48)


def banner(message: str) -> None:
    # No explicit foreground color: bold-only renders in the terminal's default text
    # color, which stays high-contrast on both light and dark backgrounds. (Picking
    # a saturated color would also visually clash with the actor colors below.)
    with _PRINT_LOCK:
        print(f"\n{Ansi.BOLD}{'═' * 78}\n  {message}\n{'═' * 78}{Ansi.RESET}")

