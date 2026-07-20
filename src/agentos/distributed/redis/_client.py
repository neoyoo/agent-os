from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
)


class AsyncRedisClient:
    """封装原生 async Redis client 的所有权、错误和 wire normalization。"""

    def __init__(self, url: str | None, client: object | None) -> None:
        self._client = client if client is not None else _create_client(url)
        self._owns_client = client is None
        self._closed = False

    def ensure_open(self) -> None:
        if self._closed:
            raise DistributedStoreClosedError()

    async def call(
        self,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        self.ensure_open()
        try:
            method = getattr(self._client, method_name)
            return await method(*args, **kwargs)
        except Exception:
            raise DeliveryUnavailableError() from None

    async def ensure_consumer_group(self, stream: str, group: str) -> None:
        self.ensure_open()
        try:
            method = getattr(self._client, "xgroup_create")
            await method(stream, group, id="0-0", mkstream=True)
        except Exception as error:
            if "BUSYGROUP" in str(error):
                return
            raise DeliveryUnavailableError() from None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._owns_client:
            return
        try:
            await getattr(self._client, "aclose")()
        except Exception:
            raise DeliveryUnavailableError() from None


def decode_text(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def field_text(value: object, name: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return decode_text(value.get(name, value.get(name.encode("utf-8"))))


def integer_field(value: object, name: str) -> int:
    if not isinstance(value, Mapping):
        raise DeliveryUnavailableError()
    raw = value.get(name, value.get(name.encode("utf-8")))
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError:
            raise DeliveryUnavailableError() from None
    if type(raw) is int:
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            pass
    raise DeliveryUnavailableError()


def pending_delivery_counts(value: object) -> dict[str, int]:
    if not isinstance(value, (list, tuple)):
        raise DeliveryUnavailableError()
    result: dict[str, int] = {}
    for row in value:
        message_id = field_text(row, "message_id")
        count = integer_field(row, "times_delivered")
        if message_id is None or count < 1:
            raise DeliveryUnavailableError()
        result[message_id] = count + 1
    return result


def stream_rows(value: object) -> list[tuple[str, object]]:
    if not value:
        return []
    rows = value
    if isinstance(value, (list, tuple)):
        first = value[0]
        if (
            isinstance(first, (list, tuple))
            and len(first) == 2
            and isinstance(first[1], (list, tuple))
        ):
            rows = first[1]
    if not isinstance(rows, (list, tuple)):
        raise DeliveryUnavailableError()
    parsed: list[tuple[str, object]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise DeliveryUnavailableError()
        cursor = decode_text(row[0])
        if cursor is None:
            raise DeliveryUnavailableError()
        parsed.append((cursor, row[1]))
    return parsed


def stream_id_key(value: str) -> tuple[int, int]:
    try:
        milliseconds, sequence = value.split("-", 1)
        return int(milliseconds), int(sequence)
    except (TypeError, ValueError):
        raise DeliveryUnavailableError() from None


def _create_client(url: str | None) -> object:
    if not url:
        raise ValueError("url is required when client is not provided")
    try:
        from redis import asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    except Exception:
        raise DistributedBackendUnavailableError() from None
