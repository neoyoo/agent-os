from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import OutboxClaim, OutboxRecord
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


class BlockingSecondPublishQueue(FakeQueue):
    def __init__(self, trace: list[str]) -> None:
        super().__init__(trace)
        self.second_publish_started = asyncio.Event()

    async def publish(self, *, record: OutboxRecord) -> str:
        if len(self.published) == 1:
            self.second_publish_started.set()
            await asyncio.Event().wait()
        return await super().publish(record=record)


class BatchOutbox(FakeOutbox):
    def __init__(
        self,
        trace: list[str],
        claims: tuple[OutboxClaim, ...],
        *,
        mark_failure_at: int | None = None,
    ) -> None:
        super().__init__(trace, claims[0])
        self.claims = claims
        self.mark_failure_at = mark_failure_at
        self.mark_attempts = 0

    async def claim_batch(
        self,
        *,
        owner_id: str,
        limit: int,
        ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]:
        self.trace.append("outbox.claim")
        self.claim_calls += 1
        return self.claims

    async def mark_published(
        self,
        *,
        claim: OutboxClaim,
        queue_entry_id: str,
    ) -> None:
        self.trace.append("outbox.mark")
        self.mark_attempts += 1
        if self.mark_attempts == self.mark_failure_at:
            raise RuntimeError("mark interrupted")
        self.marked.append((claim, queue_entry_id))


class ReleaseFailingBatchOutbox(BatchOutbox):
    def __init__(self, trace: list[str], claims: tuple[OutboxClaim, ...]) -> None:
        super().__init__(trace, claims, mark_failure_at=1)
        self.release_attempts: list[OutboxClaim] = []

    async def release_claim(self, *, claim: OutboxClaim) -> None:
        self.release_attempts.append(claim)
        if len(self.release_attempts) == 1:
            raise RuntimeError("release interrupted")
        await super().release_claim(claim=claim)


class BlockingReleaseBatchOutbox(BatchOutbox):
    def __init__(self, trace: list[str], claims: tuple[OutboxClaim, ...]) -> None:
        super().__init__(trace, claims, mark_failure_at=1)
        self.release_attempts: list[OutboxClaim] = []

    async def release_claim(self, *, claim: OutboxClaim) -> None:
        self.release_attempts.append(claim)
        await asyncio.Event().wait()


class FailingThenBlockingReleaseBatchOutbox(BatchOutbox):
    def __init__(self, trace: list[str], claims: tuple[OutboxClaim, ...]) -> None:
        super().__init__(trace, claims, mark_failure_at=1)
        self.release_attempts: list[OutboxClaim] = []

    async def release_claim(self, *, claim: OutboxClaim) -> None:
        self.release_attempts.append(claim)
        if len(self.release_attempts) == 1:
            raise RuntimeError("release interrupted")
        await asyncio.Event().wait()


class CancellableThenBlockingReleaseBatchOutbox(BatchOutbox):
    def __init__(self, trace: list[str], claims: tuple[OutboxClaim, ...]) -> None:
        super().__init__(trace, claims, mark_failure_at=1)
        self.release_attempts: list[OutboxClaim] = []
        self.release_started = asyncio.Event()

    async def release_claim(self, *, claim: OutboxClaim) -> None:
        self.release_attempts.append(claim)
        if len(self.release_attempts) == 1:
            self.release_started.set()
        await asyncio.Event().wait()


def batch_claims() -> tuple[OutboxClaim, ...]:
    claim = outbox_claim()
    return tuple(
        replace(
            claim,
            record=replace(claim.record, outbox_id=f"outbox_{index}"),
            claim_id=f"relay_claim_{index}",
        )
        for index in range(1, 4)
    )


def build_batch_relay(
    trace: list[str],
    *,
    mark_failure_at: int | None = None,
    queue: FakeQueue | None = None,
) -> tuple[OutboxRelay, BatchOutbox, FakeQueue]:
    outbox = BatchOutbox(
        trace,
        batch_claims(),
        mark_failure_at=mark_failure_at,
    )
    selected_queue = queue or FakeQueue(trace)
    relay = OutboxRelay(
        outbox=outbox,
        queue=selected_queue,
        owner_id="relay_1",
        batch_size=10,
        claim_ttl=timedelta(seconds=30),
    )
    return relay, outbox, selected_queue


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
        assert outbox.released == [outbox.claim]
        assert outbox.marked == []

        assert await relay.relay_once() == 1
        assert queue.published == [outbox.claim.record, outbox.claim.record]
        assert outbox.marked == [(outbox.claim, "entry-2")]

    asyncio.run(scenario())


def test_mark_failure_releases_current_and_unprocessed_batch_claims() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, outbox, queue = build_batch_relay(trace, mark_failure_at=2)

        with pytest.raises(RuntimeError, match="mark interrupted"):
            await relay.relay_once()

        assert queue.published == [
            outbox.claims[0].record,
            outbox.claims[1].record,
        ]
        assert outbox.marked == [(outbox.claims[0], "entry-1")]
        assert outbox.released == list(outbox.claims[1:])

    asyncio.run(scenario())


def test_cancelled_batch_releases_current_and_unprocessed_claims() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = BlockingSecondPublishQueue(trace)
        relay, outbox, _ = build_batch_relay(trace, queue=queue)
        batch = asyncio.create_task(relay.relay_once())
        await queue.second_publish_started.wait()

        batch.cancel()

        with pytest.raises(asyncio.CancelledError):
            await batch
        assert outbox.marked == [(outbox.claims[0], "entry-1")]
        assert outbox.released == list(outbox.claims[1:])

    asyncio.run(scenario())


def test_release_failure_does_not_skip_remaining_batch_claims() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claims = batch_claims()
        outbox = ReleaseFailingBatchOutbox(trace, claims)
        relay = OutboxRelay(
            outbox=outbox,
            queue=FakeQueue(trace),
            owner_id="relay_1",
            batch_size=10,
            claim_ttl=timedelta(seconds=30),
        )

        with pytest.raises(RuntimeError, match="mark interrupted") as raised:
            await relay.relay_once()

        assert isinstance(raised.value.__cause__, RuntimeError)
        assert str(raised.value.__cause__) == "release interrupted"
        assert outbox.release_attempts == list(claims)
        assert outbox.released == list(claims[1:])

    asyncio.run(scenario())


def test_blocking_release_cannot_exceed_the_shared_batch_deadline() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claims = batch_claims()
        outbox = BlockingReleaseBatchOutbox(trace, claims)
        relay = OutboxRelay(
            outbox=outbox,
            queue=FakeQueue(trace),
            owner_id="relay_1",
            batch_size=10,
            claim_ttl=timedelta(seconds=30),
            batch_timeout=timedelta(milliseconds=10),
        )

        with pytest.raises(DeliveryUnavailableError):
            await asyncio.wait_for(relay.relay_once(), timeout=0.2)

        assert outbox.release_attempts == list(claims)

    asyncio.run(scenario())


def test_release_deadline_takes_precedence_over_an_earlier_release_error() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claims = batch_claims()
        outbox = FailingThenBlockingReleaseBatchOutbox(trace, claims)
        relay = OutboxRelay(
            outbox=outbox,
            queue=FakeQueue(trace),
            owner_id="relay_1",
            batch_size=10,
            claim_ttl=timedelta(seconds=30),
            batch_timeout=timedelta(milliseconds=10),
        )

        with pytest.raises(DeliveryUnavailableError):
            await asyncio.wait_for(relay.relay_once(), timeout=0.2)

        assert outbox.release_attempts == list(claims)

    asyncio.run(scenario())


def test_release_cancellation_takes_precedence_over_the_batch_deadline() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claims = batch_claims()
        outbox = CancellableThenBlockingReleaseBatchOutbox(trace, claims)
        relay = OutboxRelay(
            outbox=outbox,
            queue=FakeQueue(trace),
            owner_id="relay_1",
            batch_size=10,
            claim_ttl=timedelta(seconds=30),
            batch_timeout=timedelta(milliseconds=20),
        )
        active = asyncio.create_task(relay.relay_once())
        await outbox.release_started.wait()
        active.cancel("caller stopped")

        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(active, timeout=0.2)

        assert caught.value.args == ("caller stopped",)
        assert outbox.release_attempts == list(claims)

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


def test_queued_batch_is_rejected_when_close_starts_before_lock_entry() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, _, queue = build_blocking_relay(trace)
        active = asyncio.create_task(relay.relay_once())
        await queue.publish_started.wait()

        queued = asyncio.create_task(relay.relay_once())
        await asyncio.sleep(0)
        closing = asyncio.create_task(relay.close())
        await asyncio.sleep(0)
        queue.publish_release.set()

        assert await active == 1
        with pytest.raises(RuntimeError, match="relay is closing or closed"):
            await queued
        await closing

    asyncio.run(scenario())


def test_close_waits_for_cancelled_batch_to_release_claim() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        relay, outbox, queue = build_blocking_relay(trace)
        active = asyncio.create_task(relay.relay_once())
        await queue.publish_started.wait()

        closing = asyncio.create_task(relay.close())
        await asyncio.sleep(0)
        active.cancel()

        with pytest.raises(asyncio.CancelledError):
            await active
        await closing
        assert outbox.released == [outbox.claim]
        assert outbox.marked == []
        assert trace[-1] == "outbox.release"

    asyncio.run(scenario())


def test_batch_timeout_releases_claim_and_bounds_close() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        outbox = FakeOutbox(trace, outbox_claim())
        queue = BlockingPublishQueue(trace)
        relay = OutboxRelay(
            outbox=outbox,
            queue=queue,
            owner_id="relay_1",
            batch_size=10,
            claim_ttl=timedelta(seconds=30),
            batch_timeout=timedelta(milliseconds=10),
        )

        batch = asyncio.create_task(relay.relay_once())
        await queue.publish_started.wait()
        closing = asyncio.create_task(relay.close())

        with pytest.raises(DeliveryUnavailableError):
            await batch
        await asyncio.wait_for(closing, timeout=1)
        assert outbox.released == [outbox.claim]
        assert outbox.marked == []

    asyncio.run(scenario())


def test_relay_configuration_remains_frozen_after_construction() -> None:
    relay, _, _ = build_relay([])

    for name, value in (
        ("owner_id", "relay_2"),
        ("batch_size", 1),
        ("claim_ttl", timedelta(seconds=1)),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(relay, name, value)
