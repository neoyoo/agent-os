from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.distributed.errors import DeliveryUnavailableError
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
