"""End-to-end tests: real uvicorn server in a subprocess + the actual sync client."""

from __future__ import annotations

import threading

import pytest
from pype_client import (
    ClientAuthRequest,
    ClientRequest,
    PypeClient,
    PypeForbiddenError,
    PypeTimeoutError,
    ServiceAuthRequest,
    ServiceClient,
)


def _client_settings(base_url: str):  # type: ignore[no-untyped-def]
    from pype_client.config import Settings

    return Settings(base_url=base_url, network_buffer_ms=2000)


def test_full_client_to_service_roundtrip(server_base_url: str) -> None:
    s = _client_settings(server_base_url)
    sc = ServiceClient(settings=s)
    pc = PypeClient(settings=s)

    def fake_service() -> None:
        with sc.authenticate(ServiceAuthRequest(service_name="doubler")) as svc:
            req = svc.get_request(timeout=2000)
            n = int(req.text)
            req.send_response(str(n * 2), content_type="text/plain")

    t = threading.Thread(target=fake_service, daemon=True)
    t.start()

    with pc.authenticate(ClientAuthRequest(name="alice")) as conn:
        rid = conn.send_request("doubler", "21", content_type="text/plain")
        resp = conn.get_response(rid, timeout=3000)
        assert resp.text == "42"
        assert resp.request_id == rid

    t.join(timeout=5)
    assert not t.is_alive()


def test_binary_payload_preserved(server_base_url: str) -> None:
    s = _client_settings(server_base_url)
    sc = ServiceClient(settings=s)
    pc = PypeClient(settings=s)
    payload = bytes(range(256))

    def fake_service() -> None:
        with sc.authenticate(ServiceAuthRequest(service_name="binecho")) as svc:
            req = svc.get_request(timeout=2000)
            assert req.bytes == payload
            assert req.content_type == "application/octet-stream"
            req.send_response(req.bytes, content_type="application/octet-stream")

    t = threading.Thread(target=fake_service, daemon=True)
    t.start()

    with pc.authenticate(ClientAuthRequest(name="bob")) as conn:
        rid = conn.send_request("binecho", payload, content_type="application/octet-stream")
        resp = conn.get_response(rid, timeout=3000)
        assert resp.bytes == payload
        assert resp.content_type == "application/octet-stream"
    t.join(timeout=5)


def test_out_of_order_responses(server_base_url: str) -> None:
    s = _client_settings(server_base_url)
    sc = ServiceClient(settings=s)
    pc = PypeClient(settings=s)

    def fake_service() -> None:
        # Drain both, respond in REVERSE order.
        with sc.authenticate(ServiceAuthRequest(service_name="ooo")) as svc:
            req_a = svc.get_request(timeout=2000)
            req_b = svc.get_request(timeout=2000)
            # Respond to b first, then a.
            req_b.send_response(b"B", content_type="text/plain")
            req_a.send_response(b"A", content_type="text/plain")

    t = threading.Thread(target=fake_service, daemon=True)
    t.start()

    with pc.authenticate(ClientAuthRequest(name="charlie")) as conn:
        rid_a = conn.send_request("ooo", b"a", content_type="text/plain")
        rid_b = conn.send_request("ooo", b"b", content_type="text/plain")
        # Ask for A first; client should still find it even though B comes off the wire first.
        resp_a = conn.get_response(rid_a, timeout=3000)
        resp_b = conn.get_response(rid_b, timeout=3000)
        assert resp_a.bytes == b"A"
        assert resp_b.bytes == b"B"
    t.join(timeout=5)


def test_get_response_timeout_when_no_service_responds(server_base_url: str) -> None:
    s = _client_settings(server_base_url)
    pc = PypeClient(settings=s)
    with pc.authenticate(ClientAuthRequest(name="lonely")) as conn:
        rid = conn.send_request("noone-listening", b"x", content_type="text/plain")
        with pytest.raises(PypeTimeoutError):
            conn.get_response(rid, timeout=200)


def test_send_request_503_when_service_queue_full(server_base_url: str) -> None:
    """Server's service_queue_max_size is 8 in the integration env. Fill it then overflow."""
    s = _client_settings(server_base_url)
    pc = PypeClient(settings=s)
    with pc.authenticate(ClientAuthRequest(name="filler")) as conn:
        for _ in range(8):
            conn.send_request("filler-svc", b"x", content_type="text/plain", timeout=0)
        with pytest.raises(PypeTimeoutError):
            conn.send_request("filler-svc", b"x", content_type="text/plain", timeout=0)


def test_send_response_after_client_closed_succeeds_silently(server_base_url: str) -> None:
    """In the lazy-create model, the server no longer 410s a service POST to a client
    that has logged off. The server simply lazy-creates a fresh queue, enqueues the
    response, and returns 202. The response is orphaned and the reaper will clean up
    the queue eventually — but the service's POST is not an error.

    This is the price of cluster-friendly lazy creation: a service can never tell, just
    from a single POST, whether the originating client is still listening. That's
    fine: services are stateless processors, not coordinators."""
    s = _client_settings(server_base_url)
    pc = PypeClient(settings=s)
    sc = ServiceClient(settings=s)

    conn = pc.authenticate(ClientAuthRequest(name="will-leave"))
    client_id = conn.client_id
    rid = conn.send_request("logoff-test", b"x", content_type="text/plain")
    conn.close()

    # The service drains the queue and responds. POST silently succeeds even though
    # there's no live consumer of the response.
    with sc.authenticate(ServiceAuthRequest(service_name="logoff-test")) as svc:
        req: ClientRequest = svc.get_request(timeout=2000)
        assert req.client_id == client_id
        assert req.request_id == rid
        # No exception expected; the response gets enqueued in a re-created queue
        # that nobody will ever drain.
        req.send_response(b"too late", content_type="text/plain")


def test_get_response_after_close_raises(server_base_url: str) -> None:
    s = _client_settings(server_base_url)
    pc = PypeClient(settings=s)
    conn = pc.authenticate(ClientAuthRequest(name="x"))
    conn.close()
    with pytest.raises(RuntimeError):
        conn.get_response("anything", timeout=0)


def test_get_all_responses_with_concurrent_service(server_base_url: str) -> None:
    """Submit 3 requests, one service responds to all 3, get_all_responses returns dict of 3."""
    s = _client_settings(server_base_url)
    sc = ServiceClient(settings=s)
    pc = PypeClient(settings=s)

    def fake_service() -> None:
        with sc.authenticate(ServiceAuthRequest(service_name="triple")) as svc:
            for _ in range(3):
                req = svc.get_request(timeout=2000)
                req.send_response(req.bytes + b"!", content_type="text/plain")

    t = threading.Thread(target=fake_service, daemon=True)
    t.start()

    with pc.authenticate(ClientAuthRequest(name="multi")) as conn:
        rids = [
            conn.send_request("triple", body, content_type="text/plain")
            for body in (b"a", b"b", b"c")
        ]
        results = conn.get_all_responses(*rids, timeout=3000)
        assert set(results.keys()) == set(rids)
        assert {r.bytes for r in results.values()} == {b"a!", b"b!", b"c!"}
    t.join(timeout=5)


def test_get_response_quorum(server_base_url: str) -> None:
    """Submit 3 requests; service replies to only 2; quorum=2 succeeds."""
    s = _client_settings(server_base_url)
    sc = ServiceClient(settings=s)
    pc = PypeClient(settings=s)

    def fake_service() -> None:
        with sc.authenticate(ServiceAuthRequest(service_name="quorum-svc")) as svc:
            for _ in range(2):
                req = svc.get_request(timeout=2000)
                req.send_response(req.bytes, content_type="text/plain")
            # Drain the third but don't respond.
            try:
                svc.get_request(timeout=300)
            except PypeTimeoutError:
                pass

    t = threading.Thread(target=fake_service, daemon=True)
    t.start()

    with pc.authenticate(ClientAuthRequest(name="qq")) as conn:
        rids = [
            conn.send_request("quorum-svc", body, content_type="text/plain")
            for body in (b"x", b"y", b"z")
        ]
        results = conn.get_response_quorum(2, *rids, timeout=3000)
        assert len(results) >= 2
        assert set(results.keys()).issubset(set(rids))
    t.join(timeout=5)


def test_invalid_jwt_raises_auth_error(server_base_url: str) -> None:
    """Manually instantiate a ClientConnection with a bogus token; sending should 401."""
    import requests as rq
    from pype_client.config import Settings
    from pype_client.exceptions import PypeAuthError
    from pype_client.pype_client import ClientConnection

    bogus_session = rq.Session()
    bogus_session.headers["Authorization"] = "Bearer not-a-jwt"
    conn = ClientConnection(
        base_url=server_base_url,
        name="x",
        client_id="bogus-client-id",
        access_token="not-a-jwt",
        session=bogus_session,
        settings=Settings(base_url=server_base_url, network_buffer_ms=2000),
    )
    try:
        with pytest.raises(PypeAuthError):
            conn.send_request("svc", b"x", content_type="text/plain", timeout=0)
    finally:
        conn._session.close()
        conn._closed = True


def test_path_jwt_mismatch_returns_403(server_base_url: str) -> None:
    """A client GET on someone else's path should 403."""
    s = _client_settings(server_base_url)
    pc = PypeClient(settings=s)
    a = pc.authenticate(ClientAuthRequest(name="a"))
    b = pc.authenticate(ClientAuthRequest(name="b"))
    try:
        # Forge a GET on b's client_id using a's token by mutating the connection state.
        a.client_id = b.client_id  # path will use b's id, JWT still has a's
        with pytest.raises(PypeForbiddenError):
            a.get_response("any", timeout=0)
    finally:
        a.close()
        b.close()


def test_call_one_rtt_request_response_against_real_server(server_base_url: str) -> None:
    """End-to-end test for ClientConnection.call(): the synchronous-RPC shortcut that
    leverages the server's block=1 mode to do request+response in one HTTP round-trip."""
    s = _client_settings(server_base_url)
    sc = ServiceClient(settings=s)
    pc = PypeClient(settings=s)

    def fake_service() -> None:
        with sc.authenticate(ServiceAuthRequest(service_name="rtt-svc")) as svc:
            req = svc.get_request(timeout=2000)
            req.send_response(req.bytes + b"-pong", content_type="text/plain")

    t = threading.Thread(target=fake_service, daemon=True)
    t.start()

    with pc.authenticate(ClientAuthRequest(name="rtt-caller")) as conn:
        resp = conn.call("rtt-svc", b"ping", content_type="text/plain", timeout=3000)
        assert resp.bytes == b"ping-pong"
    t.join(timeout=5)
