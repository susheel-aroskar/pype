import pytest
import responses
from pype_client import (
    ClientAuthRequest,
    PypeBadRequestError,
    PypeClient,
    PypeForbiddenError,
    PypeTimeoutError,
)
from pype_client.config import Settings


def _settings() -> Settings:
    return Settings(base_url="http://test.local", network_buffer_ms=100)


def _stub_client_auth(client_id: int = 1) -> None:
    responses.post(
        "http://test.local/auth/client",
        json={
            "access_token": "jwt",
            "token_type": "Bearer",
            "role": "client",
            "name": "alice",
            "client_id": client_id,
            "client_secret": "cs",
        },
        status=200,
    )


def _auth_conn(client_id: int = 1):  # type: ignore[no-untyped-def]
    _stub_client_auth(client_id)
    return PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="alice"))


@responses.activate
def test_send_request_returns_request_id() -> None:
    responses.post("http://test.local/services/svc", status=202)
    conn = _auth_conn()
    rid = conn.send_request("svc", b"hi")
    assert rid == "1"
    rid2 = conn.send_request("svc", b"hi2")
    assert rid2 == "2"


@responses.activate
def test_send_request_str_is_utf8_encoded() -> None:
    responses.post("http://test.local/services/svc", status=202)
    conn = _auth_conn()
    conn.send_request("svc", "héllo")
    posted = next(
        c
        for c in responses.calls
        if c.request.method == "POST" and "/services/svc" in c.request.url
    )
    assert posted.request.body == "héllo".encode()


@responses.activate
def test_send_request_503_raises_timeout() -> None:
    responses.post(
        "http://test.local/services/svc", json={"detail": "full", "code": "X"}, status=503
    )
    conn = _auth_conn()
    with pytest.raises(PypeTimeoutError):
        conn.send_request("svc", b"x", timeout=10)


@responses.activate
def test_send_request_400_raises_bad_request() -> None:
    responses.post(
        "http://test.local/services/svc", json={"detail": "bad", "code": "X"}, status=400
    )
    conn = _auth_conn()
    with pytest.raises(PypeBadRequestError):
        conn.send_request("svc", b"x")


def _stub_response(client_id: int, request_id: str, body: bytes, content_type: str) -> None:
    responses.add(
        responses.GET,
        f"http://test.local/clients/{client_id}",
        body=body,
        status=200,
        headers={"X-Pype-Request-Id": request_id, "Content-Type": content_type},
    )


@responses.activate
def test_get_response_happy_path() -> None:
    _stub_client_auth()
    _stub_response(1, "myreq", b'{"answer":42}', "application/json")
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    resp = conn.get_response("myreq", timeout=1000)
    assert resp.request_id == "myreq"
    assert resp.content_type == "application/json"
    assert resp.json() == {"answer": 42}


@responses.activate
def test_get_response_preserves_arbitrary_content_type() -> None:
    _stub_client_auth()
    _stub_response(1, "rid", b"<root/>", "application/xml; charset=utf-8")
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    resp = conn.get_response("rid", timeout=1000)
    assert resp.content_type == "application/xml; charset=utf-8"
    assert resp.bytes == b"<root/>"


@responses.activate
def test_get_response_caches_other_request_ids() -> None:
    _stub_client_auth()
    # First GET returns "other", second returns "mine".
    _stub_response(1, "other", b"OTHER", "text/plain")
    _stub_response(1, "mine", b"MINE", "text/plain")
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    resp = conn.get_response("mine", timeout=1000)
    assert resp.bytes == b"MINE"
    # The 'other' response should still be retrievable from the cache without an HTTP call.
    assert "other" in conn._cache


@responses.activate
def test_get_response_returns_cached_without_http_call() -> None:
    _stub_client_auth()
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    # Pre-populate the cache directly.
    from pype_client.messages import ServiceResponse

    conn._cache["pre"] = ServiceResponse(
        request_id="pre", content_type="text/plain", payload=b"hello"
    )
    n_calls_before = len(responses.calls)
    resp = conn.get_response("pre", timeout=0)
    assert resp.bytes == b"hello"
    # No GET happened; only the auth POST is in calls.
    assert len(responses.calls) == n_calls_before


@responses.activate
def test_get_response_raises_timeout_on_204() -> None:
    _stub_client_auth()
    responses.get("http://test.local/clients/1", status=204)
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    with pytest.raises(PypeTimeoutError):
        conn.get_response("missing", timeout=100)


@responses.activate
def test_get_response_propagates_403() -> None:
    _stub_client_auth()
    responses.get(
        "http://test.local/clients/1", json={"detail": "no", "code": "FORBIDDEN"}, status=403
    )
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    with pytest.raises(PypeForbiddenError):
        conn.get_response("any", timeout=1000)


@responses.activate
def test_get_any_responses_returns_all_currently_matching() -> None:
    _stub_client_auth()
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    from pype_client.messages import ServiceResponse

    conn._cache["a"] = ServiceResponse("a", "text/plain", b"A")
    conn._cache["b"] = ServiceResponse("b", "text/plain", b"B")
    conn._cache["c"] = ServiceResponse("c", "text/plain", b"C")
    out = conn.get_any_responses("a", "b", "z", timeout=0)
    assert set(out.keys()) == {"a", "b"}  # 'z' isn't cached; 'c' wasn't requested
    assert out["a"].bytes == b"A"
    # 'c' remains in cache; 'a', 'b' were popped.
    assert "c" in conn._cache and "a" not in conn._cache and "b" not in conn._cache


@responses.activate
def test_get_all_responses_blocks_until_all_present() -> None:
    _stub_client_auth()
    # Server delivers them in order: r2, r1.
    _stub_response(1, "r2", b"two", "text/plain")
    _stub_response(1, "r1", b"one", "text/plain")
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    out = conn.get_all_responses("r1", "r2", timeout=2000)
    assert set(out.keys()) == {"r1", "r2"}
    assert out["r1"].bytes == b"one"
    assert out["r2"].bytes == b"two"


@responses.activate
def test_get_all_responses_partial_left_in_cache_on_timeout() -> None:
    _stub_client_auth()
    _stub_response(1, "r1", b"one", "text/plain")  # only one delivered
    responses.get("http://test.local/clients/1", status=204)  # then 204s
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    with pytest.raises(PypeTimeoutError):
        conn.get_all_responses("r1", "r2", timeout=100)
    # Partial response remains for next call.
    assert "r1" in conn._cache


@responses.activate
def test_get_response_quorum_returns_when_threshold_reached() -> None:
    _stub_client_auth()
    _stub_response(1, "r1", b"1", "text/plain")
    _stub_response(1, "r2", b"2", "text/plain")
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    out = conn.get_response_quorum(2, "r1", "r2", "r3", timeout=2000)
    assert {"r1", "r2"}.issubset(out.keys())
    # 'r3' is not cached, was never returned.
    assert "r3" not in out


@responses.activate
def test_get_response_quorum_validates_quorum_arg() -> None:
    _stub_client_auth()
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    with pytest.raises(ValueError):
        conn.get_response_quorum(0, "a", "b")
    with pytest.raises(ValueError):
        conn.get_response_quorum(3, "a", "b")


@responses.activate
def test_close_calls_logoff_and_swallows_errors() -> None:
    _stub_client_auth()
    responses.delete("http://test.local/auth/client", status=500)  # simulate failure
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    conn.close()  # must not raise
    assert conn._closed is True
    with pytest.raises(RuntimeError):
        conn.send_request("svc", b"x")


@responses.activate
def test_close_is_idempotent() -> None:
    _stub_client_auth()
    responses.delete(
        "http://test.local/auth/client", status=200, json={"client_id": 1, "status": "logged_off"}
    )
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    conn.close()
    conn.close()  # second close: no-op


@responses.activate
def test_context_manager_calls_close_on_exit() -> None:
    _stub_client_auth()
    responses.delete(
        "http://test.local/auth/client", status=200, json={"client_id": 1, "status": "logged_off"}
    )
    pc = PypeClient(settings=_settings())
    with pc.authenticate(ClientAuthRequest(name="x")) as conn:
        assert conn._closed is False
    assert conn._closed is True


# ----------------------------------------------------------------------------
# call() — single-RTT synchronous RPC via server-side block=1
# ----------------------------------------------------------------------------


@responses.activate
def test_call_happy_path_returns_matching_response_in_one_rtt() -> None:
    _stub_client_auth()
    responses.post(
        "http://test.local/services/echo",
        body=b"echoed",
        status=200,
        headers={"X-Pype-Request-Id": "1", "Content-Type": "text/plain"},
    )
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    resp = conn.call("echo", "hi", content_type="text/plain", timeout=2000)
    assert resp.request_id == "1"
    assert resp.bytes == b"echoed"
    # Sanity: the POST happened, no extra GET was issued.
    post_calls = [c for c in responses.calls if c.request.method == "POST"]
    get_calls = [c for c in responses.calls if c.request.method == "GET"]
    # 1 auth POST + 1 service POST; no GET /clients call needed.
    assert len(post_calls) == 2
    assert not any("/clients/" in c.request.url for c in get_calls)


@responses.activate
def test_call_includes_block_and_timeout_query_params() -> None:
    _stub_client_auth()
    responses.post(
        "http://test.local/services/echo",
        body=b"x",
        status=200,
        headers={"X-Pype-Request-Id": "1", "Content-Type": "text/plain"},
    )
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    conn.call("echo", b"x", timeout=1500)
    posted = next(
        c
        for c in responses.calls
        if c.request.method == "POST" and "/services/echo" in c.request.url
    )
    assert "block=1" in posted.request.url
    assert "request_id=1" in posted.request.url
    # Some timeout query param should be present and start at user-supplied value
    # (could be slightly less due to monotonic-time read between mint and send).
    assert "timeout=" in posted.request.url


@responses.activate
def test_call_falls_back_to_get_response_on_202() -> None:
    """If the server returns 202 (request enqueued but no response in time), call()
    falls back to GET /clients/{id} via the standard get_response loop."""
    _stub_client_auth()
    # Phase 1: server returns 202 (request enqueued, nothing in client queue yet).
    responses.post("http://test.local/services/echo", status=202, body=b"")
    # Phase 2: GET /clients/1 returns the response for our request_id.
    responses.add(
        responses.GET,
        "http://test.local/clients/1",
        body=b"late",
        status=200,
        headers={"X-Pype-Request-Id": "1", "Content-Type": "text/plain"},
    )
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    resp = conn.call("echo", b"x", timeout=2000)
    assert resp.bytes == b"late"
    assert resp.request_id == "1"


@responses.activate
def test_call_caches_mismatched_response_and_falls_back() -> None:
    """If the server's 200 carries a DIFFERENT request_id (a stale entry from the head
    of the client's queue), call() should cache it and fall back to GET to fetch ours."""
    _stub_client_auth()
    # Phase 1: server returns 200 but for some other earlier request_id.
    responses.post(
        "http://test.local/services/echo",
        body=b"OLD",
        status=200,
        headers={"X-Pype-Request-Id": "old-rid", "Content-Type": "text/plain"},
    )
    # Phase 2: GET delivers OUR response.
    responses.add(
        responses.GET,
        "http://test.local/clients/1",
        body=b"OURS",
        status=200,
        headers={"X-Pype-Request-Id": "1", "Content-Type": "text/plain"},
    )
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    resp = conn.call("echo", b"x", timeout=2000)
    assert resp.bytes == b"OURS"
    assert resp.request_id == "1"
    # The old response should be cached for a future get_response("old-rid") call.
    assert "old-rid" in conn._cache
    assert conn._cache["old-rid"].bytes == b"OLD"


@responses.activate
def test_call_raises_timeout_on_503() -> None:
    """If the service queue stayed full, the server returns 503 and call() raises
    PypeTimeoutError. The request was never enqueued, so no fallback poll happens."""
    _stub_client_auth()
    responses.post(
        "http://test.local/services/echo",
        status=503,
        json={"detail": "queue full", "code": "X"},
    )
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    with pytest.raises(PypeTimeoutError):
        conn.call("echo", b"x", timeout=100)
    # No GET fallback for 503 — request was never enqueued.
    assert not any("/clients/" in c.request.url for c in responses.calls)


@responses.activate
def test_call_raises_timeout_when_fallback_also_exhausts_deadline() -> None:
    """Server returns 202 (no response in phase 1) and then keeps returning 204.
    With finite timeout, call() should raise PypeTimeoutError."""
    _stub_client_auth()
    responses.post("http://test.local/services/echo", status=202, body=b"")
    responses.get("http://test.local/clients/1", status=204)
    conn = PypeClient(settings=_settings()).authenticate(ClientAuthRequest(name="x"))
    with pytest.raises(PypeTimeoutError):
        conn.call("echo", b"x", timeout=100)
