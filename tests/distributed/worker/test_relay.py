from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import OutboxRecord
from agentos.distributed.worker.relay import OutboxRelay

from tests.distributed.worker._fakes import FakeOutbox, FakeQueue, outbox_claim


def build_relay(trace: list[str]) -> tuple[OutboxRelay, FakeOutbox, FakeQueue]:
    outbox = FakeOutbox(trace, outbox_claim())
    queue = FakeQueue(trace)
    relay = OutboxRelay(
        outbox=outbox,
        queue=queue,
        owner_id="relay_1",
        batch_size=10,
        claim_ttl=timedelta(seconds=30),
    )
    return relay, outbox, queue


class BlockingPublishQueue(FakeQueue):
    def __init__(self, trace: list[str]) -> None:
        super().__init__(trace)
        self.publish_started = asyncio.Event()
        self.publish_release = asyncio.Event()

    async def publish(self, *, record: OutboxRecord) -> str:
        self.publish_started.set()
        await self.publish_release.wait()
        return await super().publish(record=record)


def build_blocking_relay(
    trace: list[str],
) -> tuple[OutboxRelay, FakeOutbox, BlockingPublishQueue]:
    outbox = FakeOutbox(trace, outbox_claim())
    queue = BlockingPublishQueue(trace)
    relay = OutboxRelay(
        outbox=outbox,
        queue=queue,
        owner_id="relay_1",
        batch_size=10,
        claim_ttl=timedelta(seconds=30),
    )
    return relay, outbox, queue


def test_relay_marks_only_after_queue_publish() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, outbox, queue = build_relay(trace)

        assert await relay.relay_once() == 1
        assert trace == ["outbox.claim", "queue.publish", "outbox.mark"]
        assert queue.published == [outbox.claim.record]
        assert outbox.marked == [(outbox.claim, "entry-1")]

    asyncio.run(scenario())


def test_publish_failure_releases_outbox_claim() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, outbox, queue = build_relay(trace)
        queue.publish_error = DeliveryUnavailableError()

        with pytest.raises(DeliveryUnavailableError):
            await relay.relay_once()
        assert outbox.released == [outbox.claim]
        assert outbox.marked == []
        assert trace == ["outbox.claim", "queue.publish", "outbox.release"]

    asyncio.run(scenario())


def test_mark_failure_allows_same_outbox_to_be_published_again() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, outbox, queue = build_relay(trace)
        outbox.mark_error = RuntimeError("mark interrupted")

        with pytest.raises(RuntimeError, match="mark interrupted"):
            await relay.relay_once()
        assert outbox.released == []
        assert outbox.marked == []

        assert await relay.relay_once() == 1
        assert queue.published == [outbox.claim.record, outbox.claim.record]
        assert outbox.marked == [(outbox.claim, "entry-2")]

    asyncio.run(scenario())


def test_close_waits_for_active_batch_and_rejects_new_batches() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, _, queue = build_blocking_relay(trace)
        batch = asyncio.create_task(relay.relay_once())
        await queue.publish_started.wait()

        closing = asyncio.create_task(relay.close())
        await asyncio.sleep(0)

        assert not closing.done()
        with pytest.raises(RuntimeError, match="relay is closing or closed"):
            await relay.relay_once()

        queue.publish_release.set()
        assert await batch == 1
        await closing

        with pytest.raises(RuntimeError, match="relay is closing or closed"):
            await relay.relay_once()

    asyncio.run(scenario())


def test_cancelled_close_can_be_retried_after_active_batch() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, _, queue = build_blocking_relay(trace)
        batch = asyncio.create_task(relay.relay_once())
        await queue.publish_started.wait()

        closing = asyncio.create_task(relay.close())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(RuntimeError, match="relay is closing or closed"):
            await relay.relay_once()

        queue.publish_release.set()
        assert await batch == 1
        await relay.close()
        await relay.close()

    asyncio.run(scenario())
