Now let's implement a REST client for the server described in @server-implementation-plan.md. The client will use requests library to make all its REST calls and will always use synchronous, blocking calls. All the handling of what seems like mutiple concurrent requests and responses, async / non-blocking looking stuff is actually handled by a rather clever arrangement between the REST server and REST client internally using simple, blocking HTTP calls.

The Pype client library has two main clients,
    - A service side client used by backend services to interact with pype server
    - A client side client used by clients to interact with pype server
 + bunch of supporting utility classes.

---

# ServiceClient - A service side client.
It is used by backend services to
- Authenticate with the pype server as a backend service
- Pull (GET) requests intended for the service from pype server
- POST responses to those requests back to pype server

## ServiceAuthRequest
Utility model or schema class containing credentials etc. used to POST to Pype `/auth/service` endpoint. In this prototype it will only contain user specified service name but use pydantic schema for future exetnsibility.

## ServiceClient
Takes the service_name, host and port of the pype server in its __init__() method, if  host and port not specified, uses default/values from config settings. Contains a single `authenticate()` method.
```
    service_connection = service_client.authenticate(service_auth_request)
```
- It POSTs the `ServiceAuthRequest` to the `/auth/service` endpoint of pype server using the service_name from the constructor.
- Successful auth returns `ServiceConnection` object, otherwise throws an application-specific exception with a good, intuitive error message.
- The auth response is `Content-Type: application/json` with shape `{ "access_token", "token_type", "role", "name", "service_secret" }`. The library reads `access_token` and `name` directly from this JSON; no JWT decoding is needed.
- On successful authentication, the returned `ServiceConnection` object wraps the JWT and submits it as an `Authorization: Bearer ...` HTTP header on every subsequent request.

## ServiceConnection
Returned by successful `ServiceClient.authenticate()`, wraps a valid JWT token signed by the pype server. Stores its own `service_name` as rtruned in the `name` field of the JWT token. It exposes following methods:

### ServiceConnection.get_request(timeout = None) -> ClientRequest:
- Does GET to `/services/{service_name}` using it's own `service_name`, providing JWT token received from the server.
- Returns an instance of `ClientRequest` or throws a TimeoutError if the request times out.
- Caller can provide `timeout` in milliseconds to block, 0 time out for non-blocking / return immediately kind of operation or set it to `None` to block indefinitely

## ClientRequest
Wraps a client request pulled from (GET)  `/services/{service_name}` endpoint using `service_client.get_request()`. Fields:
- `client_id`: int (from the `X-Pype-Client-Id` response HTTP header)
- `client_secret`: string (from the `X-Pype-Client-Secret` response HTTP header)
- `request_id`: string (from the `X-Pype-Request-Id` response HTTP header)
- `content_type`: string (from the standard `Content-Type` response HTTP header)
- `request_body`: bytes (raw response body of the GET — preserves whatever the original client sent)
- `service_name`: string
- service's jwt_token

Convenience accessors (modeled on the `requests` library):
- `bytes` (property) → returns `request_body` as-is.
- `text` (lazy property) → decodes `request_body` as UTF-8. Cached after first access. Raises if undecodable.
- `json()` (lazy method) → decodes `request_body` as UTF-8 and parses as JSON. Cached after first call. Raises if not valid JSON.

It exposes the method `send_response()`. The service is responsible for picking the right accessor based on `content_type`.

## ClientRequest.send_response(response: str | bytes, content_type: str = "application/json", timeout = None)
- Does a POST to the `/clients/{client_id}` REST endpoint of the pype server, with timeout provided by the caller.
- If `response` is `str`, the library encodes it as UTF-8 bytes before sending.
- POST contains
    - client_id in the path parameter
    - client_secret as a query parameter
    - request_id as a query parameter
    - timeout as a query parameter
    - `Content-Type` HTTP request header set to `content_type`
    - request body is the response payload (bytes) to be returned to the original client
- Throws an appropriate application exception / timeout exception depending on the status code if the status code is not `202 Accepted` — for example, 410 (client gone), 400, 403, or 503 (timeout).


---

# PypeClient - A caller or client side client.
It is used by the clients or callers to
- Authenticate with the pype server as a client.
- POST requests intended for a given service to pype server
- Pull (GET) responses to those requests from the pype server

## ClientAuthRequest
Utility model or schema class containing credentials etc. used to POST to Pype `/auth/client` endpoint. In this prototype it will only contain user specified client name but use pydantic schema for future exetnsibility.


## PypeClient
Takes host and port of the pype server in its __init__() method, if not specified uses default/values from config settings. Contains a single `authenticate()` method.
```
    client_connection = pype_client.authenticate(client_auth_request)
```
- It submits the `ClientAuthRequest` to the `/auth/client` endpoint of pype server.
- Successful auth returns `ClientConnection` object, otherwise throws an application-specific exception with a good, intuitive error message.
- The auth response is `Content-Type: application/json` with shape `{ "access_token", "token_type", "role", "name", "client_id", "client_secret" }`. The library reads `access_token`, `client_id`, and `client_secret` directly from this JSON for the connection's own use; no JWT decoding is needed on the client side.
- On successful authentication, the returned `ClientConnection` object wraps the JWT and submits it as an `Authorization: Bearer ...` HTTP header on every subsequent request.

## ClientConnection
Returned by successful `PypeClient.authenticate()`, wraps a valid JWT token signed by the pype server + its own `client_name` and server generated client_id and client_secret. It exposes following methods:

### ClientConnection.log_off():
Does a DELETE request `/auth/client` endpoint of pype server with its JWT token to sign off.

### ClientConnection.send_request(service_name: str, request: str | bytes, content_type: str = "application/json", timeout = None) -> str:
- Does POST to `/services/{service_name}` providing it's own `client_id` and `client_secret` in a JWT token and a unique `request_id` as a query parameter internally. Request body is the request to be sent to the backend service.
- If `request` is `str`, the library encodes it as UTF-8 bytes before sending. The `Content-Type` HTTP request header is set to `content_type`.
- `request_id`s must be unique for every request for a given client. They are generated internally simply by incrementing a single int counter cached inside `ClientConnection` instance and then converting the incremented value to string.
- Returns the `request_id` generated and used for sending the request. It can be used as a "request handle" later to retrieve the response generated for this request using the same `ClientConnection` instance. Throws an appropriate application exception / timeout exception depending on the status code if the status code is not `202 Accepted` — for example, 400, 401, 403, or 503 (timeout).
- Caller can provide `timeout` in milliseconds to block, 0 timeout for non-blocking / return immediately kind of operation or set it to `None` to block indefinitely.

### ClientConnection.call(service_name: str, request: str | bytes, content_type: str = "application/json", timeout = None) -> ServiceResponse:
The preferred shape for **simple synchronous request/response** RPC: send a request and get back its response in a single HTTP round-trip in the happy path. Internally uses pype's `block=1` mode on `POST /services/{service_name}` (see server design doc).

Argument and encoding rules are identical to `send_request`: `str` payloads are encoded as UTF-8, `bytes` are sent as-is, `content_type` becomes the `Content-Type` HTTP request header.

Behavior:
1. Mints a fresh `request_id` and POSTs to `/services/{service_name}?request_id={rid}&block=1&timeout={remaining_ms}` with the request body.
2. **Happy path** — the server returns `200 OK` with `X-Pype-Request-Id == rid`. The library wraps the body in a `ServiceResponse` and returns it. Total cost: one HTTP round-trip.
3. **Stale-response path** — the server returns `200 OK` but with a different `X-Pype-Request-Id` (the originating client had an older response sitting at the head of its queue). The library caches that response in its internal map and falls back to step 5.
4. **Slow-service path** — the server returns `202 Accepted` with empty body. The request is on the service queue but no response landed in time. Falls back to step 5.
5. **Fallback** — the library calls `self.get_response(rid, timeout=remaining_ms)` with whatever budget is left from the original deadline. This reuses the standard get_response loop (cache lookup + GET /clients/{client_id} polling).
6. **Service queue full** — the server returns `503` (request was NOT enqueued). The library raises `PypeTimeoutError`.

The user-supplied `timeout` is the **total** budget for the whole call across both phases. `timeout=None` loops indefinitely (same semantics as `get_response*` for `None`); `timeout=0` is non-blocking on both phases.

Use `call()` for simple synchronous RPC. For fan-out / scatter-gather / quorum patterns, use `send_request` followed by one of the `get_response*` collect methods, since those patterns require keeping multiple requests in flight simultaneously.


## ServiceResponse
A small object returned by the `get_response*()` family below. Carries the response that came back for a specific request. Fields:
- `request_id`: string
- `content_type`: string (from the `Content-Type` HTTP response header)
- `payload`: bytes (raw response body — preserves whatever the backend service sent)

Convenience accessors (modeled on the `requests` library):
- `bytes` (property) → returns `payload` as-is.
- `text` (lazy property) → decodes `payload` as UTF-8. Cached after first access. Raises if undecodable.
- `json()` (lazy method) → decodes `payload` as UTF-8 and parses as JSON. Cached after first call. Raises if not valid JSON.

The internal `ClientConnection` cache (request_id → ServiceResponse) stores `ServiceResponse` instances directly.

## ClientConnection.get_response(request_id: str, timeout = None) -> ServiceResponse:
This method returns a response for the request identified by `request_id`, submitted by the client to pype server, while obeying the user specified `timeout`. It _can_ internally send a GET request to `/clients/{client_id}` REST endpoint of the pype server using the JWT token. The main things to remember about the response returned by the `/clients/{client_id}` endpoint are:
    - A GET request to `/clients/{client_id}` can return a response for a different request_id, not necessarily the one we asked for. The `request_id` of the returned response is given by the `X-Pype-Request-Id` HTTP response header. The `Content-Type` response header carries the response's media type. The body carries the raw payload bytes.
The `ClientConnection.get_response(request_id)` method internally handles this by,
    1. First checking if a `ServiceResponse` for the given `request_id` is already stored inside the internal (request_id -> ServiceResponse) map that the ClientConnection maintains. This could have been populated by any previous call to one of the `get_response*()` methods (see below). If present, it is **popped** from the internal map and returned to the caller. In this case no actual GET REST request needs to be made to the pype server.
    2. If the response is not found in the internal map, the ClientConnection sends a GET REST request to `/clients/{client_id}` with `timeout` set to the remaining deadline. The very first call calculates the final deadline as `now + timeout`. Each subsequent REST call uses the remaining time to that original deadline as its `timeout` query parameter. If the returned response has the requested `request_id`, it is returned directly to the caller (not stored). If it has some other `request_id`, a `ServiceResponse` is stored in the internal map keyed by that other `request_id` for future lookups, and the loop iterates.
    3. The loop terminates when either (a) the requested `request_id` is found, or (b) the server returns any status other than 200 OK (e.g., 204 No Content on server-side timeout, or 400 / 403 / 410), or (c) the local deadline is exhausted. In cases (b) and (c) a `TimeoutError` is raised. Cached `ServiceResponse`s for other request_ids remain in the internal map.
    - All REST requests to the pype endpoints are blocking requests with timeout.

All the other `get_response*()` methods below that look like they are doing parallel or async request/response handling are actually minor variations of the looping logic above and internally use single-threaded blocking REST calls only.

For all of them: on timeout, any `ServiceResponse`s already cached in the internal map remain there (they are NOT popped on timeout) so a future `get_response()` call for one of those request_ids can still find them. Only the responses being returned by a successful call are popped from the map.

## ClientConnection.get_any_responses(*request_ids: str, timeout = None) -> dict[str, ServiceResponse]:
Follows the same looping logic as `get_response()` but the success condition is "ANY one or more of `*request_ids` is present in the internal map". On success, **all currently-matching** request_ids in `*request_ids` are popped from the map and returned as a dict. If the local deadline is exhausted before any match, a `TimeoutError` is raised.

## ClientConnection.get_all_responses(*request_ids: str, timeout = None) -> dict[str, ServiceResponse]:
Follows the same looping logic but the success condition is "ALL of `*request_ids` are present in the internal map". On success, all of them are popped and returned as a dict (always covering every requested id). If the local deadline is exhausted before all are present, a `TimeoutError` is raised; the partial set already cached in the map is left intact for future lookups.

## ClientConnection.get_response_quorum(quorum: int, *request_ids: str, timeout = None) -> dict[str, ServiceResponse]:
Like `get_all_responses` but with a quorum threshold: success is "at least `quorum` of `*request_ids` are present". On success, **all currently-matching** request_ids in `*request_ids` are popped and returned (so the dict size is `>= quorum` and `<= len(request_ids)`). If the local deadline is exhausted before quorum is reached, a `TimeoutError` is raised; the partial set in the map is left intact.

Notes:
- We haven't put any restriction on the format of client requests and service responses. Payloads are bytes on the wire; callers send `str | bytes` and receive bytes (with `content_type` in hand to decode if needed).
- This means both client requests and service responses can exchange data in any pre-agreed format including binary (e.g., Protobuf).
- For testing we will use JSON-RPC based backend services.

Concurrency model:
- `ServiceClient` / `ServiceConnection` and `PypeClient` / `ClientConnection` are **not** thread-safe. They are intended for single-threaded, blocking use. The internal `request_id → ServiceResponse` cache on `ClientConnection`, the `request_id` counter, and the underlying `requests.Session` are all unguarded. If a caller needs concurrency, they must instantiate one connection per thread (each will get its own `client_id` from auth, so request_id namespaces don't collide).