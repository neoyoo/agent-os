from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Transport 生成的 ASGI 无关 HTTP 响应。"""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("HTTP status code is invalid")
        headers = tuple(self.headers)
        if any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
            for item in headers
        ):
            raise TypeError("HTTP response headers are invalid")
        if type(self.body) is not bytes:
            raise TypeError("HTTP response body must be bytes")
        object.__setattr__(self, "headers", headers)


__all__ = ["HttpResponse"]
