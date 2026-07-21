from __future__ import annotations

from agentos.transports.http.errors import HttpValidationError
from agentos.transports.http.request_types import HttpHeaders


def headers_from_asgi_scope(value: object) -> HttpHeaders:
    """Decode ASGI byte headers without accepting malformed scope data."""

    if not isinstance(value, (list, tuple)):
        raise HttpValidationError()
    try:
        items = tuple(
            (name.decode("ascii"), header_value.decode("utf-8"))
            for name, header_value in value
        )
    except (AttributeError, TypeError, UnicodeDecodeError, ValueError):
        raise HttpValidationError() from None
    return HttpHeaders(items)


__all__ = ["headers_from_asgi_scope"]
