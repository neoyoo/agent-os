from __future__ import annotations

import re
import unicodedata

from agentos.transports.http.errors import HttpValidationError
from agentos.transports.http.request_types import HttpHeaders


_DECIMAL = re.compile(r"0|[1-9][0-9]*")


def single_header(
    headers: HttpHeaders,
    name: str,
    *,
    required: bool,
) -> str | None:
    if type(headers) is not HttpHeaders:
        raise invalid_request()
    values = headers.get_all(name)
    if len(values) > 1 or (required and not values):
        raise invalid_request()
    return values[0] if values else None


def idempotency_key(headers: HttpHeaders) -> str:
    value = single_header(headers, "idempotency-key", required=True)
    assert value is not None
    return validate_identifier(value)


def validate_identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value != value.strip()
        or any(
            char.isspace() or unicodedata.category(char).startswith("C")
            for char in value
        )
    ):
        raise invalid_request()
    return value


def validate_content_length(headers: HttpHeaders, body_length: int) -> None:
    value = single_header(headers, "content-length", required=False)
    if value is None:
        return
    if _DECIMAL.fullmatch(value) is None or int(value) != body_length:
        raise invalid_request()


def bounded_limit(value: int, *, hard_limit: int) -> int:
    if type(value) is not int or not 0 < value <= hard_limit:
        raise ValueError("configured HTTP limit is invalid")
    return value


def parse_decimal(value: str) -> int:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise invalid_request()
    return int(value)


def invalid_request() -> HttpValidationError:
    return HttpValidationError()
