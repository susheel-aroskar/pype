# Pype - like Unix shell pipe (|) but for distributed systems.

## The main idea, in one sentence
Replace the traditional reverse proxy + load balancer betwen callers and
services with a Pype server which acts like a bi-directional pipe between
the two. Both the clients and the backend services connect to Pype using 
outbound HTTP connections to exchange requests and responses with each other.

## The architectural inversion
In a traditional setup, callers send HTTP requests to a load balancer, which
then routes them to whichever backend it believes to be the least loaded. The 
load balancer must track every backend server's health, latency, and free
capacity. Backend servers must accept inbound connections, and the network path 
from the load balancer to backend servers must be open. In cloud deployments 
where backend instances churn frequently due to autoscaling etc., the load 
balancer must also manage service discovery - adding new backends to its 
connection pool and removing old, shutdown instances. This introduces 
additional complexity such as health checks and dynamic membership management.

Pype inverts this model. Both clients and backends connect using **outbound**
HTTP connections to a single Pype server. Callers `POST` their requests to 
backed services into in-memory queues designated for those services. Backend 
service instances then *pull* their requests via blocking `GET` on their 
designated queues, process them, and `POST` the responses back into the 
original caller’s in-memory queue hosted on the same Pype server. Callers 
then retrieve the responses targeted to them using `GET` when ready.


```
       caller k                    pype                       serviceA
         |                           |                           |
         | ── POST /services/{A} ───>|<─── GET /services/A ──────|   (long poll)
         |                           |                           |
         |                           | ── client k's request ───>|
         |                           |                           |
         |                           |                (process client request)
         |                           |                           |
         |                           |<── POST /clients/{k} ─────|
         | ── GET /clients/{k} ─────>|                           |
         |                           |                           |
         | <── response for k ───────|                           |
```

This single change eliminates entire categories of distributed systems complexities.

## What falls out for free

**Service discovery.** Backends register themselves by authenticating with a
`service_name`; Pype creates the named queue if it doesn't already exist.
Adding a backend instance is just calling `authenticate(service_name)`. There's
no separate discovery system, no registry, no stale DNS cache problems.

**Optimal load balancing - without tracking load.** Backends *pull* work as
fast as they can process it. A fast worker takes more requests, a slow worker
takes fewer; the queue itself becomes the implicit load balancing primitive. 
There is no scheduler tracking backend latency or health, no "least-connections" 
or "power of two" algorithms. Whoever asks for work first gets the next 
pending request - that's it.

**Automatic back-pressure.** When a service queue fills, Pype returns
`503 Service Unavailable` on subsequent `POST`s. Callers see a `PypeTimeoutError`
and can decide whether to retry, fail fast, or shed load. No explicit
flow-control protocol is needed.

**An optimal autoscaling signal.** `GET /load/services` returns the live depth 
of every service queue. An autoscaling agent monitoring this single endpoint 
has a near-ideal load signal: queue growing → add workers; queue draining
quickly → remove some. Queue depth is a far better load indicator than CPU or
memory for I/O-bound services.

**Traffic smoothing.** Bounded queues absorb traffic spikes naturally. A 10× 
burst doesn't knock backends over; it temporarily raises queue depth, Pype 
pushes back via 503 once the cap is hit, and the autoscaler reacts.

**Time-decoupled startup.** Clients and services don't need to coordinate 
their start-up order. A client may POST to a service that hasn't started yet.
Pype creates the queue on the fly, and the request waits patiently. There's no
"wait for upstream to be healthy first" coordination.

**Network and firewall friendly.** Both clients and services initiate
*outbound* HTTP - neither side opens a listening socket. Outbound connections
sail through corporate firewalls, NATs, container networking, and proxies that
would otherwise need to be configured specifially to permit inbound traffic. 
A backend running behind a NAT can still act as a server, servicing a caller 
behind another set of reverse proxies and NATs - without any networking changes 
on either end. It just works.

## Developer ergonomics

**Single-threaded synchronous clients with rich completion semantics.**
The `pype-client` library uses plain blocking `requests` - no `asyncio`,
callbacks, futures or thread pools. Every logical request is split into two
halves the caller can interleave at will:

- `send_request(service, payload)` returns a `request_id` immediately as soon
  as the request is queued on pype.
- `get_response(request_id)`, `get_any_responses([request_ids])`, 
  `get_all_responses([request_ids])`, and `get_response_quorum(N, [request_ids])` 
  block waiting for the appropriate completion condition to be met.
- `call(service, payload)` is the one-line shortcut for simple synchronous
  RPC: send-and-receive in a **single** HTTP round-trip in the happy path,
  using pype's `block=1` mode that enqueues the request and returns the
  response in the same call.

This gives you fan-out/fan-in, first-result-wins, and majority-quorum
primitives using plain synchronous Python. The included demo test exercises all
four patterns in roughly fifty lines of code, with no `async def` or futures 
anywhere.

**Protocol and format agnostic.** Payloads are opaque bytes on the wire.
`Content-Type` is preserved end-to-end, so JSON, Protobuf, MessagePack, raw
bytes, anything works. Pype itself never parses request or response bodies.
This keeps Pype fast while supporting arbitrary message formats between clients
and backend services.

## Extensibility

Because both callers and backends are authenticated with stable identities
(`client_id`, `service_name`), extending pype with **access control** is
straightforward. An ACL attached to each service — "only client_ids in group
`payment-frontend` may post to `payment-service`" — becomes a single in-memory
lookup on `POST /services/{name}`. Sub-microsecond, no extra service to call.
The same mechanism extends naturally to per-service rate limits, per-tenant
quotas, and audit logging.

## What's in this submission

- `pype-server`: an async FastAPI app implementing the protocol described
  above. Includes a background reaper for abandoned client queues, end-to-end
  Content-Type preservation, JWT-based auth, and configurable queue sizes and
  timeouts.
- `pype-client`: a synchronous client library exposing `ServiceClient` (for
  backend services), `PypeClient` (for callers), and the four collect-side primitives.
- 131 tests passing, with `mypy --strict` and `ruff` clean.
- `client/tests/integration/test_demo.py` — a guided, color-coded demo that
  exercises every capability against a real subprocess server. Run with
  `pytest client/tests/integration/test_demo.py -s -v` then read the console 
  transcript.

## Running the demo

Assuming you have only Python 3.10+ installed, follow these steps to run the end to end demo.

**1. Clone the repo and `cd` into it.**
```bash
git clone https://github.com/susheel-aroskar/pype pype
cd pype
```

**2. Create and activate a virtual environment in the repo root.**
```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
```

**3. Install both packages, editable, with their dev extras.**
```bash
pip install --upgrade pip
pip install -e "./server[dev]" -e "./client[dev]"
```
This installs `pype-server` and `pype-client` in editable mode and pulls in
everything the demo needs .

**4. Run the demo.**
```bash
pytest client/tests/integration/test_demo.py -s -v
```
The `-s` flag is essential — without it pytest captures stdout and the
demo's narrated transcript is hidden. The demo spins up a real pype server
in a subprocess on a free port, then runs three concurrent client
threads (Alice, Bob, Carol) against three concurrent backend service
threads (echo, doubler, dice). You'll see a colorized swim-lane transcript
with service messages indented to the right, followed by
`Demo complete — all 3 callers succeeded`. The whole run takes ~2 seconds.

**Optional — run the entire test suite.**
```bash
pytest                              # all 150+ tests across both packages
ruff check .                        # lints
mypy server/src client/src          # strict type-check
```

## Honest scope

This is still a prototype. The pype server keeps all queues in process memory. A
real production deployment may want persistence for in-flight responses or HA
replication of the pype server itself. Neither is conceptually difficult to add
on top of the protocol described here - but both are deliberately kept out of scope
for this prototype.
