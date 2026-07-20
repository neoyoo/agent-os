import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import OutboxRecord, RequestScope
from agentos.distributed.redis.queue import RedisQueueAdapter

from _fake_redis import FakeAsyncRedis


SCOPE = RequestScope("tenant_1", "user_1")


def _record(outbox_id: str, topic: str = "run_wakeup") -> OutboxRecord:
    return OutboxRecord(
        scope=SCOPE,
        outbox_id=outbox_id,
        topic=topic,
        payload={"kind": "wakeup"},
        created_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
    )


def test_queue_uses_redis_ids_and_deduplicates_stable_outbox_ids() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        queue = RedisQueueAdapter(
            client=redis,
            group_name="workers",
            key_prefix="test",
        )

        second_id = await queue.publish(record=_record("outbox_2"))
        first_id = await queue.publish(record=_record("outbox_1"))
        duplicate_id = await queue.publish(record=_record("outbox_2"))

        assert redis.xadd_ids == ["*", "*", "*"]
        assert second_id != first_id != duplicate_id
        fields = redis.stream_fields("test:queue:run_wakeup")
        assert [item["outbox_id"] for item in fields] == [
            "outbox_2",
            "outbox_1",
            "outbox_2",
        ]

        deliveries = await queue.receive(
            topic="run_wakeup",
            consumer_id="worker_1",
            limit=10,
        )
        assert [delivery.outbox_id for delivery in deliveries] == [
            "outbox_2",
            "outbox_1",
        ]

        for delivery in deliveries:
            await queue.ack(topic="run_wakeup", delivery=delivery)
        assert await redis.xpending("test:queue:run_wakeup", "workers") == {
            "pending": 0,
            "min": None,
            "max": None,
            "consumers": [],
        }

    asyncio.run(scenario())


def test_queue_reclaims_unacked_wakeup_with_incremented_delivery_count() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        queue = RedisQueueAdapter(client=redis, group_name="workers")
        await queue.publish(record=_record("outbox_1"))

        original = await queue.receive(
            topic="run_wakeup",
            consumer_id="worker_1",
            limit=1,
        )
        redis.advance_pending(5_000)
        reclaimed = await queue.reclaim(
            topic="run_wakeup",
            consumer_id="worker_2",
            min_idle=timedelta(seconds=1),
            limit=1,
        )

        assert reclaimed[0].delivery_id == original[0].delivery_id
        assert reclaimed[0].outbox_id == "outbox_1"
        assert reclaimed[0].delivery_count == 2
        await queue.ack(topic="run_wakeup", delivery=reclaimed[0])

    asyncio.run(scenario())


def test_queue_acks_later_duplicate_after_original_outbox_is_committed() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        queue = RedisQueueAdapter(client=redis, group_name="workers")
        await queue.publish(record=_record("outbox_1"))
        original = await queue.receive(
            topic="run_wakeup",
            consumer_id="worker_1",
            limit=1,
        )
        await queue.ack(topic="run_wakeup", delivery=original[0])

        await queue.publish(record=_record("outbox_1"))
        duplicate = await queue.receive(
            topic="run_wakeup",
            consumer_id="worker_2",
            limit=1,
        )

        assert duplicate == ()
        summary = await redis.xpending("agentos:queue:run_wakeup", "workers")
        assert summary["pending"] == 0

    asyncio.run(scenario())


def test_queue_trimming_never_removes_pending_entries() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        queue = RedisQueueAdapter(
            client=redis,
            group_name="workers",
            key_prefix="test",
            max_entries=2,
        )
        for index in range(4):
            await queue.publish(record=_record(f"outbox_{index}"))

        deliveries = await queue.receive(
            topic="run_wakeup",
            consumer_id="worker_1",
            limit=3,
        )
        oldest_pending = deliveries[0].delivery_id
        await queue.ack(topic="run_wakeup", delivery=deliveries[1])
        await queue.ack(topic="run_wakeup", delivery=deliveries[2])
        await queue.publish(record=_record("outbox_4"))

        stream_ids = [
            message_id
            for message_id, _ in redis.streams["test:queue:run_wakeup"]
        ]
        assert oldest_pending in stream_ids

    asyncio.run(scenario())


def test_queue_close_cancels_blocking_receive_and_outages_are_typed() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        queue = RedisQueueAdapter(client=redis, block_ms=30_000)
        blocked = asyncio.create_task(
            queue.receive(
                topic="run_wakeup",
                consumer_id="worker_1",
                limit=1,
            ),
        )
        await redis.read_started.wait()
        await queue.close()

        with pytest.raises(DistributedStoreClosedError):
            await blocked

        failing = FakeAsyncRedis()
        failing.fail = True
        unavailable = RedisQueueAdapter(client=failing)
        with pytest.raises(DeliveryUnavailableError) as caught:
            await unavailable.publish(record=_record("outbox_1"))
        assert "secret" not in str(caught.value)

    asyncio.run(scenario())
