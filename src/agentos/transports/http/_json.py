from __future__ import annotations

import json
import math
from typing import Any

from agentos.transports.http._headers import (
    bounded_limit,
    invalid_request,
    single_header,
    validate_content_length,
)
from agentos.transports.http.errors import (
    HttpParseError,
    HttpValidationError,
    RequestTooLargeError,
    UnsupportedMediaTypeError,
)
from agentos.transports.http.request_types import HttpHeaders


MAX_JSON_BODY_BYTES = 256 * 1024
MAX_JSON_NESTING = 32


def decode_json_object(
    headers: HttpHeaders,
    body: bytes,
    *,
    max_body_bytes: int,
) -> dict[str, Any]:
    if type(headers) is not HttpHeaders or type(body) is not bytes:
        raise invalid_request()
    limit = bounded_limit(max_body_bytes, hard_limit=MAX_JSON_BODY_BYTES)
    if len(body) > limit:
        raise RequestTooLargeError()
    validate_content_length(headers, len(body))
    content_type = single_header(headers, "content-type", required=False)
    if not _is_json_content_type(content_type):
        raise UnsupportedMediaTypeError()
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except RecursionError as error:
        raise HttpValidationError() from error
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HttpParseError() from error
    if type(payload) is not dict:
        raise invalid_request()
    try:
        _validate_json_value(payload, depth=1)
    except _NonFiniteNumberError as error:
        raise HttpParseError() from error
    except ValueError as error:
        raise invalid_request() from error
    return payload


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise HttpValidationError() from error


class _NonFiniteNumberError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _validate_json_value(value: Any, *, depth: int) -> None:
    if type(value) is float and not math.isfinite(value):
        raise _NonFiniteNumberError("JSON number must be finite")
    if type(value) is dict:
        if depth > MAX_JSON_NESTING:
            raise ValueError("JSON nesting exceeds limit")
        for item in value.values():
            _validate_json_value(item, depth=depth + 1)
    elif type(value) is list:
        if depth > MAX_JSON_NESTING:
            raise ValueError("JSON nesting exceeds limit")
        for item in value:
            _validate_json_value(item, depth=depth + 1)


def _is_json_content_type(value: str | None) -> bool:
    if value is None:
        return False
    parts = tuple(part.strip().lower() for part in value.split(";"))
    return parts in {("application/json",), ("application/json", "charset=utf-8")}
