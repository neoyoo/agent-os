from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.errors import DistributedStoreClosedError
from agentos.distributed.models import OutboxRecord, QueueDelivery
from agentos.distributed._model_validation import require_identifier, require_positive
from agentos.distributed.redis._client import (
    AsyncRedisClient,
    decode_text,
    field_text,
    pending_delivery_counts,
    stream_rows,
)
from agentos.distributed.redis._queue_scripts import (
    commit_delivery,
    reserve_delivery,
    trim_safely,
)


class RedisQueueAdapter:
    """基于 Redis Streams consumer group 的 at-least-once delivery Adapter。"""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: object | None = None,
        key_prefix: str = "agentos",
        group_name: str = "agentos-workers",
        block_ms: int = 1_000,
        max_entries: int = 10_000,
        dedup_ttl_seconds: int = 86_400,
    ) -> None:
        require_identifier(key_prefix, "key_prefix")
        require_identifier(group_name, "group_name")
        require_positive(block_ms, "block_ms")
        require_positive(max_entries, "max_entries")
        require_positive(dedup_ttl_seconds, "dedup_ttl_seconds")
        self._redis = AsyncRedisClient(url, client)
        self._key_prefix = key_prefix.rstrip(":")
        self._group_name = group_name
        self._block_ms = block_ms
        self._max_entries = max_entries
        self._dedup_ttl_seconds = dedup_ttl_seconds
        self._closed = False
        self._closing = False
        self._close_lock = asyncio.Lock()
        self._blocked_reads: set[asyncio.Task[Any]] = set()

    async def publish(self, *, record: OutboxRecord) -> str:
        self._ensure_open()
        if type(record) is not OutboxRecord:
            raise TypeError("record must be OutboxRecord")
        await self._ensure_group(record.topic)
        delivery_id = decode_text(
            await self._redis.call(
                "xadd",
                self._stream_key(record.topic),
                {"outbox_id": record.outbox_id},
                id="*",
            ),
        )
        if delivery_id is None:
            raise DeliveryUnavailableError()
        await self._trim_safely(record.topic)
        return delivery_id

    async def receive(
        self,
        *,
        topic: str,
        consumer_id: str,
        limit: int,
    ) -> tuple[QueueDelivery, ...]:
        self._validate_read(topic, consumer_id, limit)
        await self._ensure_group(topic)
        response = await self._blocking_call(
            "xreadgroup",
            self._group_name,
            consumer_id,
            {self._stream_key(topic): ">"},
            count=limit,
            block=self._block_ms,
            noack=False,
        )
        return await self._track_deliveries(topic, stream_rows(response), {})

    async def reclaim(
        self,
        *,
        topic: str,
        consumer_id: str,
        min_idle: timedelta,
        limit: int,
    ) -> tuple[QueueDelivery, ...]:
        self._validate_read(topic, consumer_id, limit)
        idle_ms = _duration_milliseconds(min_idle, "min_idle")
        await self._ensure_group(topic)
        stream = self._stream_key(topic)
        pending = await self._redis.call(
            "xpending_range",
            stream,
            self._group_name,
            "-",
            "+",
            limit,
            idle=idle_ms,
        )
        counts = pending_delivery_counts(pending)
        if not counts:
            return ()
        rows = stream_rows(
            await self._redis.call(
                "xclaim",
                stream,
                self._group_name,
                consumer_id,
                idle_ms,
                list(counts),
            ),
        )
        return await self._track_deliveries(topic, rows, counts)

    async def ack(self, *, topic: str, delivery: QueueDelivery) -> None:
        self._ensure_open()
        require_identifier(topic, "topic")
        if type(delivery) is not QueueDelivery:
            raise TypeError("delivery must be QueueDelivery")
        await commit_delivery(
            self._redis,
            stream=self._stream_key(topic),
            group=self._group_name,
            outbox_id=delivery.outbox_id,
            delivery_id=delivery.delivery_id,
            ttl_seconds=self._dedup_ttl_seconds,
        )
        await self._trim_safely(topic)

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            try:
                blocked = tuple(self._blocked_reads)
                for task in blocked:
                    task.cancel()
                if blocked:
                    await asyncio.gather(*blocked, return_exceptions=True)
                await self._redis.close()
                self._closed = True
            finally:
                self._closing = False

    async def _track_deliveries(
        self,
        topic: str,
        rows: list[tuple[str, object]],
        counts: dict[str, int],
    ) -> tuple[QueueDelivery, ...]:
        deliveries: list[QueueDelivery] = []
        discarded_duplicate = False
        for delivery_id, fields in rows:
            outbox_id = field_text(fields, "outbox_id")
            if outbox_id is None:
                raise DeliveryUnavailableError()
            state = await reserve_delivery(
                self._redis,
                stream=self._stream_key(topic),
                group=self._group_name,
                outbox_id=outbox_id,
                delivery_id=delivery_id,
            )
            if state == "reserved":
                deliveries.append(
                    QueueDelivery(delivery_id, outbox_id, counts.get(delivery_id, 1)),
                )
            else:
                discarded_duplicate = True
        if discarded_duplicate:
            await self._trim_safely(topic)
        return tuple(deliveries)

    async def _trim_safely(self, topic: str) -> None:
        await trim_safely(
            self._redis,
            stream=self._stream_key(topic),
            max_entries=self._max_entries,
        )

    async def _blocking_call(
        self,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        self._ensure_open()
        task = asyncio.create_task(self._redis.call(method_name, *args, **kwargs))
        self._blocked_reads.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if self._closed or self._closing:
                raise DistributedStoreClosedError() from None
            raise
        finally:
            self._blocked_reads.discard(task)

    async def _ensure_group(self, topic: str) -> None:
        await self._redis.ensure_consumer_group(
            self._stream_key(topic),
            self._group_name,
        )

    def _validate_read(self, topic: str, consumer_id: str, limit: int) -> None:
        self._ensure_open()
        require_identifier(topic, "topic")
        require_identifier(consumer_id, "consumer_id")
        require_positive(limit, "limit")

    def _stream_key(self, topic: str) -> str:
        return f"{self._key_prefix}:queue:{quote(topic, safe='')}"

    def _ensure_open(self) -> None:
        if self._closed or self._closing:
            raise DistributedStoreClosedError()


def _duration_milliseconds(value: timedelta, field_name: str) -> int:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be timedelta")
    milliseconds = int(value.total_seconds() * 1000)
    if milliseconds <= 0:
        raise ValueError(f"{field_name} must be positive")
    return milliseconds
