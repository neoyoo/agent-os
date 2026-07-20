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
    integer_field,
    pending_delivery_counts,
    stream_id_key,
    stream_rows,
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
        self._blocked_reads: set[asyncio.Task[Any]] = set()
        self._inflight: dict[tuple[str, str], set[str]] = {}
        self._delivery_outbox: dict[tuple[str, str], str] = {}

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
        for delivery_id, _ in rows:
            self._forget_delivery(topic, delivery_id)
        return await self._track_deliveries(topic, rows, counts)

    async def ack(self, *, topic: str, delivery: QueueDelivery) -> None:
        self._ensure_open()
        require_identifier(topic, "topic")
        if type(delivery) is not QueueDelivery:
            raise TypeError("delivery must be QueueDelivery")
        outbox_key = (topic, delivery.outbox_id)
        delivery_ids = self._inflight.get(outbox_key, {delivery.delivery_id})
        await self._redis.call(
            "set",
            self._dedup_key(topic, delivery.outbox_id),
            "1",
            ex=self._dedup_ttl_seconds,
        )
        await self._redis.call(
            "xack",
            self._stream_key(topic),
            self._group_name,
            *sorted(delivery_ids, key=stream_id_key),
        )
        for delivery_id in tuple(delivery_ids):
            self._delivery_outbox.pop((topic, delivery_id), None)
        self._inflight.pop(outbox_key, None)
        await self._trim_safely(topic)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        blocked = tuple(self._blocked_reads)
        for task in blocked:
            task.cancel()
        if blocked:
            await asyncio.gather(*blocked, return_exceptions=True)
        await self._redis.close()

    async def _track_deliveries(
        self,
        topic: str,
        rows: list[tuple[str, object]],
        counts: dict[str, int],
    ) -> tuple[QueueDelivery, ...]:
        deliveries: list[QueueDelivery] = []
        committed_duplicates: list[str] = []
        for delivery_id, fields in rows:
            outbox_id = field_text(fields, "outbox_id")
            if outbox_id is None:
                raise DeliveryUnavailableError()
            if await self._redis.call("get", self._dedup_key(topic, outbox_id)):
                committed_duplicates.append(delivery_id)
                continue
            outbox_key = (topic, outbox_id)
            inflight = self._inflight.setdefault(outbox_key, set())
            is_first = not inflight
            inflight.add(delivery_id)
            self._delivery_outbox[(topic, delivery_id)] = outbox_id
            if is_first:
                deliveries.append(
                    QueueDelivery(delivery_id, outbox_id, counts.get(delivery_id, 1)),
                )
        if committed_duplicates:
            await self._redis.call(
                "xack",
                self._stream_key(topic),
                self._group_name,
                *committed_duplicates,
            )
            await self._trim_safely(topic)
        return tuple(deliveries)

    async def _trim_safely(self, topic: str) -> None:
        stream = self._stream_key(topic)
        if await self._redis.call("xlen", stream) <= self._max_entries:
            return
        groups = await self._redis.call("xinfo_groups", stream)
        if not isinstance(groups, (list, tuple)):
            raise DeliveryUnavailableError()
        boundaries: list[str] = []
        for group in groups:
            group_name = field_text(group, "name")
            last_delivered = field_text(group, "last-delivered-id")
            if group_name is None or last_delivered is None:
                raise DeliveryUnavailableError()
            summary = await self._redis.call("xpending", stream, group_name)
            boundary = (
                field_text(summary, "min")
                if integer_field(summary, "pending")
                else last_delivered
            )
            if boundary is None or boundary == "0-0":
                return
            boundaries.append(boundary)
        if boundaries:
            await self._redis.call(
                "xtrim",
                stream,
                minid=min(boundaries, key=stream_id_key),
                approximate=False,
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
            if self._closed:
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

    def _forget_delivery(self, topic: str, delivery_id: str) -> None:
        outbox_id = self._delivery_outbox.pop((topic, delivery_id), None)
        if outbox_id is None:
            return
        key = (topic, outbox_id)
        delivery_ids = self._inflight.get(key)
        if delivery_ids is None:
            return
        delivery_ids.discard(delivery_id)
        if not delivery_ids:
            self._inflight.pop(key, None)

    def _stream_key(self, topic: str) -> str:
        return f"{self._key_prefix}:queue:{quote(topic, safe='')}"

    def _dedup_key(self, topic: str, outbox_id: str) -> str:
        return f"{self._stream_key(topic)}:acked:{quote(outbox_id, safe='')}"

    def _ensure_open(self) -> None:
        if self._closed:
            raise DistributedStoreClosedError()


def _duration_milliseconds(value: timedelta, field_name: str) -> int:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be timedelta")
    milliseconds = int(value.total_seconds() * 1000)
    if milliseconds <= 0:
        raise ValueError(f"{field_name} must be positive")
    return milliseconds
