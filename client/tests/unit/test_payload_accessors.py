import json

import pytest
from pype_client.messages import ServiceResponse


def test_bytes_returns_payload_as_is() -> None:
    r = ServiceResponse(
        request_id="r1", content_type="application/octet-stream", payload=b"\x00\xff"
    )
    assert r.bytes == b"\x00\xff"


def test_text_decodes_utf8_lazily_and_caches() -> None:
    r = ServiceResponse(request_id="r1", content_type="text/plain", payload="héllo".encode())
    assert r.text == "héllo"
    # Subsequent access doesn't redecode (we can't directly observe, but ensure same str id).
    assert r.text is r.text


def test_text_raises_on_invalid_utf8() -> None:
    r = ServiceResponse(request_id="r1", content_type="text/plain", payload=b"\xff\xfe\xfd")
    with pytest.raises(UnicodeDecodeError):
        _ = r.text


def test_json_parses_and_caches() -> None:
    payload = json.dumps({"a": 1, "b": [2, 3]}).encode()
    r = ServiceResponse(request_id="r1", content_type="application/json", payload=payload)
    assert r.json() == {"a": 1, "b": [2, 3]}
    assert r.json() is r.json()  # cached


def test_json_raises_on_invalid() -> None:
    r = ServiceResponse(request_id="r1", content_type="application/json", payload=b"not json")
    with pytest.raises(json.JSONDecodeError):
        r.json()
