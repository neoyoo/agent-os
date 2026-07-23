from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
    DistributedShutdownTimeoutError,
    DistributedStoreClosedError,
)


class AsyncRedisClient:
    """封装原生 async Redis client 的所有权、错误和 wire normalization。"""

    def __init__(
        self,
        url: str | None,
        client: object | None,
        *,
        operation_timeout: timedelta = timedelta(seconds=5),
    ) -> None:
        if type(operation_timeout) is not timedelta or operation_timeout <= timedelta(0):
            raise ValueError("operation_timeout must be positive")
        self._client = client if client is not None else _create_client(url)
        self._owns_client = client is None
        self._operation_timeout = operation_timeout.total_seconds()
        self._closed = False
        self._closing = False
        self._close_lock = asyncio.Lock()

    def ensure_open(self) -> None:
        if self._closed or self._closing:
            raise DistributedStoreClosedError()

    async def call(
        self,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        return await self._call(
            self._operation_timeout,
            method_name,
            *args,
            **kwargs,
        )

    async def blocking_call(
        self,
        block_ms: int,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        timeout = max(self._operation_timeout, block_ms / 1_000 + 1.0)
        return await self._call(timeout, method_name, *args, **kwargs)

    async def _call(
        self,
        timeout: float,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        self.ensure_open()
        try:
            method = getattr(self._client, method_name)
            async with asyncio.timeout(timeout):
                return await method(*args, **kwargs)
        except Exception:
            raise DeliveryUnavailableError() from None

    async def ensure_consumer_group(self, stream: str, group: str) -> None:
        self.ensure_open()
        try:
            method = getattr(self._client, "xgroup_create")
            async with asyncio.timeout(self._operation_timeout):
                await method(stream, group, id="0-0", mkstream=True)
        except Exception as error:
            if "BUSYGROUP" in str(error):
                return
            raise DeliveryUnavailableError() from None

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            try:
                if self._owns_client:
                    try:
                        async with asyncio.timeout(self._operation_timeout):
                            await getattr(self._client, "aclose")()
                    except TimeoutError:
                        raise DistributedShutdownTimeoutError() from None
                    except Exception:
                        raise DeliveryUnavailableError() from None
                self._closed = True
            finally:
                self._closing = False


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
