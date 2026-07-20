import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import OutboxRecord, QueueDelivery, RequestScope
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


class CreateGroupAfterSnapshotRedis(FakeAsyncRedis):
    def __init__(self) -> None:
        super().__init__()
        self.create_group_during_trim = False

    async def xinfo_groups(self, name: str) -> object:
        groups = await super().xinfo_groups(name)
        if self.create_group_during_trim:
            self.create_group_during_trim = False
            self.groups[(name, "group_b")] = "0-0"
            self.pending[(name, "group_b")] = {}
        return groups

    async def before_atomic_trim(self, name: str) -> None:
        if self.create_group_during_trim:
            self.create_group_during_trim = False
            self.groups[(name, "group_b")] = "0-0"
            self.pending[(name, "group_b")] = {}


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
        redis.advance_pending(5_000)
        assert await queue.reclaim(
            topic="run_wakeup",
            consumer_id="worker_1",
            min_idle=timedelta(seconds=1),
            limit=10,
        ) == ()
        assert await redis.xpending("test:queue:run_wakeup", "workers") == {
            "pending": 0,
            "min": None,
            "max": None,
            "consumers": [],
        }
        command_shapes = {
            (script.splitlines()[1], numkeys, len(args))
            for script, numkeys, args in redis.eval_calls
            if "agentos:queue:" in script
        }
        assert command_shapes == {
            ("-- agentos:queue:reserve:v1", 3, 5),
            ("-- agentos:queue:ack:v1", 3, 6),
            ("-- agentos:queue:trim:v1", 1, 2),
        }

    asyncio.run(scenario())


def test_queue_deduplicates_same_group_across_adapter_instances() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        first_queue = RedisQueueAdapter(client=redis, group_name="workers")
        second_queue = RedisQueueAdapter(client=redis, group_name="workers")
        await first_queue.publish(record=_record("outbox_1"))
        await first_queue.publish(record=_record("outbox_1"))

        canonical = await first_queue.receive(
            topic="run_wakeup",
            consumer_id="worker_1",
            limit=1,
        )
        duplicate = await second_queue.receive(
            topic="run_wakeup",
            consumer_id="worker_2",
            limit=1,
        )

        assert len(canonical) == 1
        assert duplicate == ()
        pending = await redis.xpending("agentos:queue:run_wakeup", "workers")
        assert pending["pending"] == 1

        await first_queue.ack(topic="run_wakeup", delivery=canonical[0])
        pending = await redis.xpending("agentos:queue:run_wakeup", "workers")
        assert pending["pending"] == 0
        redis.advance_pending(5_000)
        assert await second_queue.reclaim(
            topic="run_wakeup",
            consumer_id="worker_2",
            min_idle=timedelta(seconds=1),
            limit=10,
        ) == ()
        pending = await redis.xpending("agentos:queue:run_wakeup", "workers")
        assert pending["pending"] == 0

    asyncio.run(scenario())


def test_queue_reclaims_canonical_after_older_duplicate_was_reserved_late() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        queue = RedisQueueAdapter(client=redis, group_name="workers", block_ms=10)
        await queue.publish(record=_record("outbox_1"))
        await queue.publish(record=_record("outbox_1"))

        raw_rows = await redis.xreadgroup(
            "workers",
            "slow_worker",
            {"agentos:queue:run_wakeup": ">"},
            count=1,
            block=10,
            noack=False,
        )
        older_id = raw_rows[0][1][0][0]
        canonical = await queue.receive(
            topic="run_wakeup",
            consumer_id="crashed_worker",
            limit=1,
        )
        redis.advance_pending(5_000)

        assert await queue.reclaim(
            topic="run_wakeup",
            consumer_id="recovery_worker",
            min_idle=timedelta(seconds=1),
            limit=1,
        ) == ()
        reclaimed = await queue.reclaim(
            topic="run_wakeup",
            consumer_id="recovery_worker",
            min_idle=timedelta(seconds=1),
            limit=1,
        )

        assert older_id != canonical[0].delivery_id
        assert reclaimed[0].delivery_id == canonical[0].delivery_id

    asyncio.run(scenario())


def test_queue_ack_rejects_delivery_from_another_outbox() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        queue = RedisQueueAdapter(client=redis, group_name="workers")
        await queue.publish(record=_record("outbox_1"))
        await queue.publish(record=_record("outbox_2"))
        first, second = await queue.receive(
            topic="run_wakeup",
            consumer_id="worker_1",
            limit=2,
        )
        await queue.ack(topic="run_wakeup", delivery=first)
        await queue.ack(topic="run_wakeup", delivery=first)

        mismatched = QueueDelivery(
            second.delivery_id,
            first.outbox_id,
            second.delivery_count,
        )
        with pytest.raises(DeliveryUnavailableError):
            await queue.ack(topic="run_wakeup", delivery=mismatched)

        pending = await redis.xpending("agentos:queue:run_wakeup", "workers")
        assert pending["pending"] == 1
        processing_key = (
            "agentos:queue:run_wakeup:group:workers:processing:outbox_2"
        )
        assert redis.values[processing_key] == second.delivery_id
        await queue.ack(topic="run_wakeup", delivery=second)
        assert processing_key not in redis.values

    asyncio.run(scenario())


def test_queue_deduplication_is_scoped_to_consumer_group() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        first_group = RedisQueueAdapter(client=redis, group_name="group_a")
        second_group = RedisQueueAdapter(client=redis, group_name="group_b")
        await first_group.publish(record=_record("outbox_1"))
        await redis.xgroup_create(
            "agentos:queue:run_wakeup",
            "group_b",
            id="0-0",
        )

        first = await first_group.receive(
            topic="run_wakeup",
            consumer_id="worker_a",
            limit=1,
        )
        await first_group.ack(topic="run_wakeup", delivery=first[0])
        second = await second_group.receive(
            topic="run_wakeup",
            consumer_id="worker_b",
            limit=1,
        )

        assert [delivery.outbox_id for delivery in second] == ["outbox_1"]

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


def test_queue_trim_includes_group_created_during_boundary_calculation() -> None:
    async def scenario() -> None:
        redis = CreateGroupAfterSnapshotRedis()
        seed_queue = RedisQueueAdapter(
            client=redis,
            group_name="group_a",
            max_entries=100,
        )
        for index in range(5):
            await seed_queue.publish(record=_record(f"outbox_{index}"))
        consumed = await seed_queue.receive(
            topic="run_wakeup",
            consumer_id="worker_a",
            limit=5,
        )
        for delivery in consumed:
            await seed_queue.ack(topic="run_wakeup", delivery=delivery)

        redis.create_group_during_trim = True
        trimming_queue = RedisQueueAdapter(
            client=redis,
            group_name="group_a",
            max_entries=2,
        )
        await trimming_queue.publish(record=_record("outbox_5"))
        second_group = RedisQueueAdapter(client=redis, group_name="group_b")
        deliveries = await second_group.receive(
            topic="run_wakeup",
            consumer_id="worker_b",
            limit=10,
        )

        assert [delivery.outbox_id for delivery in deliveries] == [
            "outbox_0",
            "outbox_1",
            "outbox_2",
            "outbox_3",
            "outbox_4",
            "outbox_5",
        ]

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
