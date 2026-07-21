from __future__ import annotations

import json

import pytest

from agentos.transports.http._json import MAX_JSON_BODY_BYTES, decode_json_object
from agentos.transports.http.errors import HttpValidationError
from agentos.transports.http.request_types import HttpHeaders


HEADERS = HttpHeaders((('Content-Type', 'application/json'),))


def _nested_object(depth: int) -> bytes:
    value: object = "leaf"
    for _ in range(depth):
        value = {"value": value}
    return json.dumps(value).encode()


def test_json_depth_counts_root_object_as_one() -> None:
    assert decode_json_object(
        HEADERS,
        _nested_object(32),
        max_body_bytes=MAX_JSON_BODY_BYTES,
    )
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_json_object(
            HEADERS,
            _nested_object(33),
            max_body_bytes=MAX_JSON_BODY_BYTES,
        )


def test_json_parser_recursion_limit_is_a_validation_error() -> None:
    body = b'{"value":' + (b"[" * 2000) + b"0" + (b"]" * 2000) + b"}"

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_json_object(
            HEADERS,
            body,
            max_body_bytes=MAX_JSON_BODY_BYTES,
        )
