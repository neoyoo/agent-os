from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from collections.abc import Iterable

from agentos.transports.http.errors import HttpValidationError


_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_CONTENT_LENGTH = re.compile(r"0|[1-9][0-9]*")
_SINGLE_VALUE_HEADERS = frozenset(
    {
        "authorization",
        "a2a-version",
        "a2a-extensions",
        "idempotency-key",
        "last-event-id",
        "content-type",
        "content-length",
        "x-request-id",
    },
)
_ASCII_LIMITS = {
    "authorization": 8192,
    "a2a-extensions": 4096,
    "last-event-id": 1024,
    "a2a-version": 32,
    "content-length": 20,
    "x-request-id": 255,
}


@dataclass(frozen=True, slots=True, init=False)
class HttpHeaders:
    """保留重复项并按 ASCII 大小写不敏感查询的 HTTP headers。"""

    items: tuple[tuple[str, str], ...] = field(repr=False)

    def __init__(self, items: Iterable[tuple[str, str]]) -> None:
        normalized: list[tuple[str, str]] = []
        counts: dict[str, int] = {}
        for item in items:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("header item must be a name/value tuple")
            name, value = item
            if (
                type(name) is not str
                or _HEADER_NAME.fullmatch(name) is None
                or not name.isascii()
            ):
                raise HttpValidationError()
            lowered = name.lower()
            if type(value) is not str or any(
                unicodedata.category(char).startswith("C") for char in value
            ):
                raise HttpValidationError()
            _validate_header_value(lowered, value)
            counts[lowered] = counts.get(lowered, 0) + 1
            if lowered in _SINGLE_VALUE_HEADERS and counts[lowered] > 1:
                raise HttpValidationError()
            normalized.append((lowered, value))
        object.__setattr__(self, "items", tuple(normalized))

    def get_all(self, name: str) -> tuple[str, ...]:
        """返回指定 header 的全部值，不执行 last-wins。"""

        if type(name) is not str or not name.isascii():
            raise HttpValidationError()
        lowered = name.lower()
        return tuple(value for key, value in self.items if key == lowered)


def _validate_header_value(name: str, value: str) -> None:
    ascii_limit = _ASCII_LIMITS.get(name)
    if ascii_limit is not None:
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as error:
            raise HttpValidationError() from error
        if len(encoded) > ascii_limit:
            raise HttpValidationError()
    elif len(value.encode("utf-8")) > 255:
        raise HttpValidationError()
    if name == "content-length" and _CONTENT_LENGTH.fullmatch(value) is None:
        raise HttpValidationError()


@dataclass(frozen=True, slots=True)
class ArtifactUploadRequest:
    """通过 multipart 解码得到的单文件 Artifact 上传输入。"""

    session_id: str
    upload_id: str
    data: bytes
    filename: str | None
    media_type: str


@dataclass(frozen=True, slots=True)
class ArtifactListRequest:
    """Artifact metadata 分页查询参数。"""

    cursor: str | None
    limit: int


__all__ = [
    "ArtifactListRequest",
    "ArtifactUploadRequest",
    "HttpHeaders",
]
