# Pype — A Push-Pull Hub/relay for Service-to-Service RPC

## The main idea, in one sentence

Replace the traditional reverse proxy + load balancer with a Pype server which 
acts as central hub or relay. Both clients and backend services connect to it
using outbound HTTP connections to exchange messages with each other.

## The architectural inversion

In a traditional setup, callers send HTTP requests to a load balancer, which
routes them to whichever backend it believes to be the least loaded. The load
balancer must track every backend's health, latency, and free capacity.
Backends must accept inbound connections, and the network path from the load 
balancer to backend must remain open. In cloud deployments where backend instances
churn frequently due to autoscaling, the load balancer must also manage
service discovery — i.e., adding new backends to its connection pool and
removing old or shutting-down instances. This introduces additional
complexity such as health checks and dynamic membership management.

Pype inverts this model. Both callers and backends connect using **outbound**
HTTP connections to a single Pype server. Callers `POST` their requests into
in-memory queues designated per backend service. Backend service instances
*pull* requests via long-polling, blocking `GET` on their designated queue,
process them, and `POST` responses into the originating caller’s per-client
in-memory queue hosted on the Pype server. Callers then retrieve responses
targeted to them using `GET` when ready.


```
       caller k                    pype                       serviceA
         |                           |                           |
         | ── POST /services/{A} ───>| <── GET /services/A ──────|   (long poll)
         |                           |                           |
         |                           | ── client k's request ───>|
         |                           |                       (process)
         |                           |<── POST /clients/{k} ─────|
         | ── GET /clients/{k} ─────>|                           |
         |                           |                           |
         | <── response for k ───────|                           |
```

This single change eliminates entire categories of distributed-systems plumbing.

## What falls out for free

**Service discovery.** Backends register themselves by authenticating with a
`service_name`; pype creates the named queue if it doesn't already exist.
Adding a backend instance is just calling `authenticate(service_name)`. There's
no separate discovery system, no registry, no DNS dance.

**Optimal load balancing — without tracking load.** Backends *pull* work as
fast as they can process it. A fast worker takes more requests, a slow worker
takes fewer; the queue itself becomes the implicit balancing primitive. There
is no scheduler tracking backend latency or health, no consistent hashing, no
"least-connections" or "power of two" algorithms. Whoever asks for work first 
gets the next pending request — that's it.

**Automatic back-pressure.** When a service queue fills, pype returns
`503 Service Unavailable` on subsequent `POST`s. Callers see a `PypeTimeoutError`
and can decide whether to retry, fail fast, or shed load. No explicit
flow-control protocol is needed.

**An autoscaling signal in one endpoint.** `GET /load/services` returns the
live depth of every service queue. An autoscaling agent monitoring this single
endpoint has a near-ideal load signal: queue growing → add workers; queue draining
quickly → remove some. Queue depth is a far better load indicator than CPU or
memory for I/O-bound services.

**Traffic smoothing.** Bounded queues absorb traffic spikes. A 10× burst doesn't
knock backends over; it temporarily raises queue depth, Pype pushes back via 503
once the cap is hit, and the autoscaler reacts.

**Time-decoupled startup.** Clients and services don't need to coordinate their
start-up order. A client may POST to a service that hasn't authenticated yet — 
Pype creates the queue on the fly, and the request waits patiently. There's no
"wait for upstream to be healthy first" coordination.

**Network and firewall friendly.** Both clients and services initiate
*outbound* HTTP — neither side opens a listening socket. Outbound connections
sail through corporate firewalls, NATs, container networking, and proxies that
would otherwise need to be configured to permit inbound traffic. A backend
running behind a NAT can still act as a server, servicing a caller behind 
another set of reverse proxies and NATs — without any networking changes on
either end. It just works.

## Developer ergonomics

**Single-threaded synchronous clients with rich completion semantics.**
The `pype-client` library uses plain blocking `requests` — no `asyncio`, no
callbacks, no futures, no thread pools. Every logical request is split into two
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
This keeps pype fast while supporting arbitrary message formats between clients
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

## Honest scope

This is still a prototype. The pype server keeps all queues in process memory. A
real production deployment may want persistence for in-flight responses or HA
replication of the pype server itself. Neither is conceptually difficult to add
on top of the protocol described here — but both are deliberately kept out of scope
for this prototype.
