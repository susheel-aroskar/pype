"""Mixin providing requests-style `.bytes` / `.text` / `.json()` accessors over a `payload` field.

Designed to be reused by both `ClientRequest` (service-side) and `ServiceResponse` (client-side),
which both wrap raw bytes + a content_type and want the same lazy decoding ergonomics.
"""

from __future__ import annotations

import json
from typing import Any


class _PayloadAccessors:
    """Provides `.bytes`, `.text` (lazy), `.json()` (lazy) over a `payload: bytes` field.

    Subclasses must define a `payload: bytes` attribute. Caches the decoded text and parsed
    JSON on first access to amortize cost when the caller checks more than one accessor.
    """

    payload: bytes  # provided by subclass

    # NOTE: `_text_cache` and `_json_cache` are populated lazily. We use two sentinels so
    # `None` is a valid cached value.
    _UNSET = object()

    def __init__(self) -> None:
        self._text_cache: Any = _PayloadAccessors._UNSET
        self._json_cache: Any = _PayloadAccessors._UNSET

    @property
    def bytes(self) -> bytes:
        return self.payload

    @property
    def text(self) -> str:
        if self._text_cache is _PayloadAccessors._UNSET:
            self._text_cache = self.payload.decode("utf-8")
        return self._text_cache  # type: ignore[no-any-return]

    def json(self) -> Any:  # noqa: ANN401  JSON's return type is genuinely arbitrary
        if self._json_cache is _PayloadAccessors._UNSET:
            self._json_cache = json.loads(self.text)
        return self._json_cache
