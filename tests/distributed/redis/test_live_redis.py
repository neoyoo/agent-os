import asyncio
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import (
    LiveContentDelta,
    OutboxRecord,
    QueueDelivery,
    ReplayBatch,
    RequestScope,
    RunEventEnvelope,
    StreamGap,
)
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter


REDIS_URL = os.environ.get("AGENTOS_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    REDIS_URL is None,
    reason="set AGENTOS_TEST_REDIS_URL to run live Redis tests",
)
SCOPE = RequestScope("tenant_1", "user_1")
NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _event(sequence: int) -> RunEventEnvelope:
    return RunEventEnvelope(
        tenant_id=SCOPE.tenant_id,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        execution_attempt=1,
        event_sequence=sequence,
        event=LiveContentDelta(sequence, f"content-{sequence}"),
        occurred_at=NOW,
    )


def _record(outbox_id: str, topic: str) -> OutboxRecord:
    return OutboxRecord(
        scope=SCOPE,
        outbox_id=outbox_id,
        topic=topic,
        payload={"kind": "wakeup"},
        created_at=NOW,
    )


async def _redis_client():
    from redis.asyncio import Redis

    return Redis.from_url(REDIS_URL, decode_responses=True)


async def _delete_prefix(client: object, prefix: str) -> None:
    keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]  # type: ignore[attr-defined]
    if keys:
        await client.delete(*keys)  # type: ignore[attr-defined]


def test_live_replay_parses_eval_rows_and_reports_trimmed_gap() -> None:
    async def scenario() -> None:
        client = await _redis_client()
        prefix = f"agentos_live_{uuid4().hex}"
        replay = RedisEventReplayAdapter(client=client, key_prefix=prefix, max_events=2)
        try:
            first = await replay.append(scope=SCOPE, event=_event(0))
            second = await replay.append(scope=SCOPE, event=_event(1))
            third = await replay.append(scope=SCOPE, event=_event(2))

            assert await replay.replay(
                scope=SCOPE,
                session_id="session_1",
                run_id="run_1",
                after=first.cursor,
                limit=10,
            ) == StreamGap(
                SCOPE.tenant_id,
                "session_1",
                "run_1",
                first.cursor,
                second.cursor,
                "trimmed",
            )
            assert await replay.replay(
                scope=SCOPE,
                session_id="session_1",
                run_id="run_1",
                after=second.cursor,
                limit=10,
            ) == ReplayBatch((third,), third.cursor)
        finally:
            await replay.close()
            await _delete_prefix(client, prefix)
            await client.aclose()

    asyncio.run(scenario())


def test_live_queue_deduplicates_across_adapters_and_preserves_new_group() -> None:
    async def scenario() -> None:
        client = await _redis_client()
        prefix = f"agentos_live_{uuid4().hex}"
        topic = "run_wakeup"
        stream = f"{prefix}:queue:{topic}"
        first_queue = RedisQueueAdapter(
            client=client,
            key_prefix=prefix,
            group_name="workers",
            block_ms=10,
        )
        second_queue = RedisQueueAdapter(
            client=client,
            key_prefix=prefix,
            group_name="workers",
            block_ms=10,
        )
        try:
            await first_queue.publish(record=_record("outbox_1", topic))
            await first_queue.publish(record=_record("outbox_1", topic))
            canonical = await first_queue.receive(
                topic=topic,
                consumer_id="worker_1",
                limit=1,
            )
            duplicate = await second_queue.receive(
                topic=topic,
                consumer_id="worker_2",
                limit=1,
            )
            assert len(canonical) == 1
            assert duplicate == ()

            await first_queue.ack(topic=topic, delivery=canonical[0])
            await asyncio.sleep(0.01)
            assert await second_queue.reclaim(
                topic=topic,
                consumer_id="worker_2",
                min_idle=timedelta(milliseconds=1),
                limit=10,
            ) == ()
            assert (await client.xpending(stream, "workers"))["pending"] == 0

            trim_topic = "trim_wakeup"
            trim_stream = f"{prefix}:queue:{trim_topic}"
            trim_queue = RedisQueueAdapter(
                client=client,
                key_prefix=prefix,
                group_name="group_a",
                block_ms=10,
                max_entries=2,
            )
            for index in range(4):
                await trim_queue.publish(
                    record=_record(f"trim_outbox_{index}", trim_topic),
                )
            consumed = await trim_queue.receive(
                topic=trim_topic,
                consumer_id="worker_a",
                limit=10,
            )
            await client.xgroup_create(trim_stream, "group_b", id="0-0")
            for delivery in consumed:
                await trim_queue.ack(topic=trim_topic, delivery=delivery)

            second_group = RedisQueueAdapter(
                client=client,
                key_prefix=prefix,
                group_name="group_b",
                block_ms=10,
            )
            preserved = await second_group.receive(
                topic=trim_topic,
                consumer_id="worker_b",
                limit=10,
            )
            assert [item.outbox_id for item in preserved] == [
                "trim_outbox_0",
                "trim_outbox_1",
                "trim_outbox_2",
                "trim_outbox_3",
            ]
        finally:
            await first_queue.close()
            await second_queue.close()
            await _delete_prefix(client, prefix)
            await client.aclose()

    asyncio.run(scenario())


def test_live_queue_reclaims_canonical_after_older_noncanonical_duplicate() -> None:
    async def scenario() -> None:
        client = await _redis_client()
        prefix = f"agentos_live_{uuid4().hex}"
        topic = "run_wakeup"
        stream = f"{prefix}:queue:{topic}"
        queue = RedisQueueAdapter(
            client=client,
            key_prefix=prefix,
            group_name="workers",
            block_ms=10,
        )
        try:
            await queue.publish(record=_record("outbox_1", topic))
            await queue.publish(record=_record("outbox_1", topic))
            raw_rows = await client.xreadgroup(
                "workers",
                "slow_worker",
                {stream: ">"},
                count=1,
                block=10,
            )
            older_id = raw_rows[0][1][0][0]
            canonical = await queue.receive(
                topic=topic,
                consumer_id="crashed_worker",
                limit=1,
            )
            await asyncio.sleep(0.01)

            assert await queue.reclaim(
                topic=topic,
                consumer_id="recovery_worker",
                min_idle=timedelta(milliseconds=1),
                limit=1,
            ) == ()
            reclaimed = await queue.reclaim(
                topic=topic,
                consumer_id="recovery_worker",
                min_idle=timedelta(milliseconds=1),
                limit=1,
            )

            assert older_id != canonical[0].delivery_id
            assert reclaimed[0].delivery_id == canonical[0].delivery_id
        finally:
            await queue.close()
            await _delete_prefix(client, prefix)
            await client.aclose()

    asyncio.run(scenario())


def test_live_queue_ack_binds_committed_marker_to_canonical_delivery() -> None:
    async def scenario() -> None:
        client = await _redis_client()
        prefix = f"agentos_live_{uuid4().hex}"
        topic = "run_wakeup"
        stream = f"{prefix}:queue:{topic}"
        queue = RedisQueueAdapter(
            client=client,
            key_prefix=prefix,
            group_name="workers",
            block_ms=10,
            dedup_ttl_seconds=60,
        )
        try:
            await queue.publish(record=_record("outbox_1", topic))
            await queue.publish(record=_record("outbox_2", topic))
            first, second = await queue.receive(
                topic=topic,
                consumer_id="worker_1",
                limit=2,
            )
            await queue.ack(topic=topic, delivery=first)
            await queue.ack(topic=topic, delivery=first)
            committed_key = f"{stream}:group:workers:committed:outbox_1"
            assert await client.get(committed_key) == first.delivery_id
            assert 0 < await client.ttl(committed_key) <= 60

            mismatched = QueueDelivery(
                second.delivery_id,
                first.outbox_id,
                second.delivery_count,
            )
            with pytest.raises(DeliveryUnavailableError):
                await queue.ack(topic=topic, delivery=mismatched)

            processing_key = f"{stream}:group:workers:processing:outbox_2"
            assert await client.get(processing_key) == second.delivery_id
            assert (await client.xpending(stream, "workers"))["pending"] == 1
            await queue.ack(topic=topic, delivery=second)
            assert await client.get(processing_key) is None
        finally:
            await queue.close()
            await _delete_prefix(client, prefix)
            await client.aclose()

    asyncio.run(scenario())


def test_live_replay_close_cancels_blocking_tail() -> None:
    async def scenario() -> None:
        client = await _redis_client()
        prefix = f"agentos_live_{uuid4().hex}"
        replay = RedisEventReplayAdapter(
            client=client,
            key_prefix=prefix,
            block_ms=30_000,
        )
        subscription = replay.follow(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=None,
        )
        waiting = asyncio.create_task(anext(subscription))
        try:
            await asyncio.sleep(0.05)
            await asyncio.wait_for(replay.close(), timeout=1)
            with pytest.raises(StopAsyncIteration):
                await waiting
        finally:
            await subscription.aclose()
            await replay.close()
            await _delete_prefix(client, prefix)
            await client.aclose()

    asyncio.run(scenario())
