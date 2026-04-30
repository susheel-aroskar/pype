# pype — A Push-Pull Rendezvous for Service-to-Service RPC

## The idea, in one sentence

Replace the reverse proxy and load balancer with a single server that buffers
between callers and backends, and let both sides initiate outbound HTTP
connections to it.

## The architectural inversion

In a traditional setup, callers send HTTP to a load balancer, which routes to
whichever backend it thinks is least loaded. The load balancer must track every
backend's health, latency, and free capacity; backends must accept inbound
connections; the network path from caller to backend must stay open.

pype inverts this. Both caller and backend connect *outbound* to a single pype
server. The caller `POST`s its request onto a named in-memory queue. A backend
*pulls* the request via a long-poll `GET`, processes it, and `POST`s the
response onto the originating caller's per-caller queue. The caller pulls the
response with a `GET` whenever it's ready.

```
       caller                  pype                   service
         |                       |                       |
         | ── POST /services ──> | <── GET /services ────|   (long poll)
         |                       | ───── request ──────> |
         |                       |                   (process)
         |                       | <── POST /clients ────|
         | ── GET /clients ────> |                       |
         | <─────── response ─── |                       |
```

That single change eliminates entire categories of distributed-systems plumbing.

## What falls out for free

**Service discovery.** Backends register themselves by authenticating with a
`service_name`; pype creates the named queue if it doesn't already exist.
Adding a backend instance is just calling `authenticate(service_name)`. There's
no separate discovery system, no registry, no DNS dance.

**Optimal load balancing — without tracking load.** Backends *pull* work as
fast as they can process it. A fast worker takes more requests, a slow worker
takes fewer; the queue itself becomes the implicit balancing primitive. There
is no scheduler tracking backend latency or health, no consistent hashing, no
"least-connections" algorithm. Whoever asks for work first gets the next
request — that's it.

**Automatic back-pressure.** When a service queue fills, pype returns
`503 Service Unavailable` on further `POST`s. Callers see a `PypeTimeoutError`
and can decide whether to retry, fail fast, or shed load. No explicit
flow-control protocol is needed.

**An autoscaling signal in one endpoint.** `GET /load/services` returns the
live depth of every service queue. An autoscaling agent monitoring this single
endpoint has a near-perfect signal: queue growing → add workers; queue draining
quickly → remove some. Queue depth is a far better load indicator than CPU or
memory for I/O-bound services.

**Burst smoothing.** Bounded queues absorb traffic spikes. A 10× burst doesn't
knock backends over; it temporarily raises queue depth, pushes back via 503
once the cap is hit, and the autoscaler reacts.

**Time-decoupled startup.** Clients and services don't need to coordinate
boot order. A client may POST to a service that hasn't authenticated yet — pype
creates the queue on the fly, and the request waits patiently. There's no
"wait for upstream healthy" dance.

**Network and firewall friendliness.** Both clients and services initiate
*outbound* HTTP — neither side opens a listening socket. Outbound connections
sail through corporate firewalls, NATs, container networking, and proxies that
would otherwise need to be configured to permit inbound traffic. A backend
running behind a NAT can serve a caller behind two reverse proxies — without
any networking changes on either end.

## Developer ergonomics

**Single-threaded synchronous clients with rich completion semantics.**
The `pype-client` library uses plain blocking `requests` — no `asyncio`, no
callbacks, no futures, no thread pools. Every logical request is split into two
halves the caller can interleave at will:

- `send_request(service, payload)` returns a `request_id` immediately as soon
  as the request is queued on pype.
- `get_response(rid)`, `get_any_responses(*rids)`, `get_all_responses(*rids)`,
  and `get_response_quorum(N, *rids)` block waiting for the appropriate
  completion condition.
- `call(service, payload)` is the one-line shortcut for simple synchronous
  RPC: send-and-receive in a **single** HTTP round-trip in the happy path,
  using pype's `block=1` mode that enqueues the request and returns the
  response in the same call.

This gives you fan-out/fan-in, first-result-wins, and majority-quorum
primitives in plain synchronous Python. The included demo test exercises all
four patterns in roughly fifty lines of code, with no `async def` anywhere.

**Protocol- and format-agnostic.** Payloads are opaque bytes on the wire.
`Content-Type` is preserved end-to-end, so JSON, Protobuf, MessagePack, raw
bytes, anything works. pype itself never parses a request or response body.

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
  backends), `PypeClient` (for callers), and the four collect-side primitives.
- 131 tests passing, with `mypy --strict` and `ruff` clean.
- `client/tests/integration/test_demo.py` — a guided, color-coded demo that
  exercises every capability against a real subprocess server. Run with
  `pytest client/tests/integration/test_demo.py -s -v` and read the transcript.

## Honest scope

This is a prototype. The pype server keeps all queues in process memory, so a
production deployment would want persistence for in-flight responses and HA
replication of the pype server itself. Neither is conceptually difficult to add
on top of the protocol described here — but both are deliberately out of scope
for the take-home assignment.
