from __future__ import annotations

import asyncio

import pytest

from agentos.distributed.internal_models import InternalRunInputReceipt
from agentos.distributed.models import QueueDelivery, RequestScope, RunSubmissionReceipt
from agentos.multi.team_errors import StaleTeamDeliveryClaimError
from agentos.multi.team_identity import team_submission_id
from tests.distributed.worker.test_team_routing import (
    FakeInternalSubmissions,
    FakeRunQueries,
)
from tests.distributed.worker.test_team_runner import (
    FakeBootstrap,
    FakeDeliveries,
    FakeQueue,
    FakeTeams,
    _active_member,
    _claimed,
    _runner,
    _target,
)
from tests.planning._async import async_test


class HeartbeatGate:
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, _: float) -> None:
        self.waiting.set()
        await self.release.wait()


class BlockingInternalSubmissions(FakeInternalSubmissions):
    def __init__(self, receipt: RunSubmissionReceipt) -> None:
        super().__init__([receipt])
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def submit(
        self,
        scope: RequestScope,
        submission: object,
        authority: object,
    ) -> RunSubmissionReceipt:
        self.calls.append((scope, submission, authority))
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.completed.set()
        outcome = self.outcomes.pop(0)
        assert isinstance(outcome, RunSubmissionReceipt)
        return outcome


class BlockingRunQueries(FakeRunQueries):
    def __init__(self) -> None:
        super().__init__([None])
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def get_active(self, scope: RequestScope, session_id: str):
        self.calls.append((scope, session_id))
        self.started.set()
        await self.release.wait()
        return self.values.pop(0)


class BlockingHeartbeatDeliveries(FakeDeliveries):
    def __init__(self, claimed) -> None:
        super().__init__(claimed)
        self.heartbeat_started = asyncio.Event()
        self.heartbeat_release = asyncio.Event()

    async def heartbeat(self, *, scope, claim, ttl):
        self.heartbeat_calls.append(claim)
        self.heartbeat_started.set()
        await self.heartbeat_release.wait()
        raise StaleTeamDeliveryClaimError()


def _receipt(target, *, duplicate: bool = False) -> RunSubmissionReceipt:
    submission_id = team_submission_id(
        scope=RequestScope("tenant_1", "team_delivery_service"),
        delivery_id=target.delivery.delivery_id,
        target_session_id=target.delivery.target_session_id,
    )
    return RunSubmissionReceipt(
        "session_2",
        "run_2",
        submission_id,
        1,
        duplicate,
    )


@async_test
async def test_heartbeat_loss_allows_started_service_to_finish_without_commit() -> None:
    target = _target()
    heartbeat = HeartbeatGate()
    internal = BlockingInternalSubmissions(_receipt(target))
    deliveries = FakeDeliveries(_claimed(target))
    deliveries.heartbeat_error = StaleTeamDeliveryClaimError()
    queue = FakeQueue()
    runner, _, _ = _runner(
        bootstrap=FakeBootstrap([target]),
        deliveries=deliveries,
        queue=queue,
        teams=FakeTeams(_active_member()),
        run_queries=FakeRunQueries([None]),
        internal_submissions=internal,
        heartbeat_wait=heartbeat,
    )
    delivery = QueueDelivery("1-0", target.outbox_id, 1)

    task = asyncio.create_task(runner.run_delivery(delivery))
    await internal.started.wait()
    await heartbeat.waiting.wait()
    heartbeat.release.set()
    while not deliveries.heartbeat_calls:
        await asyncio.sleep(0)

    for _ in range(10):
        await asyncio.sleep(0)
    assert not task.done()
    assert not internal.cancelled.is_set()
    internal.release.set()
    await internal.completed.wait()

    with pytest.raises(StaleTeamDeliveryClaimError):
        await asyncio.wait_for(task, timeout=1)

    assert not internal.cancelled.is_set()
    assert deliveries.results == []
    assert deliveries.release_calls == []
    assert queue.acked == []


@async_test
async def test_route_completion_does_not_cancel_in_flight_heartbeat_failure() -> None:
    target = _target()
    heartbeat = HeartbeatGate()
    internal = BlockingInternalSubmissions(_receipt(target))
    deliveries = BlockingHeartbeatDeliveries(_claimed(target))
    queue = FakeQueue()
    runner, _, _ = _runner(
        bootstrap=FakeBootstrap([target]),
        deliveries=deliveries,
        queue=queue,
        teams=FakeTeams(_active_member()),
        run_queries=FakeRunQueries([None]),
        internal_submissions=internal,
        heartbeat_wait=heartbeat,
    )

    task = asyncio.create_task(
        runner.run_delivery(QueueDelivery("1-0", target.outbox_id, 1))
    )
    await internal.started.wait()
    await heartbeat.waiting.wait()
    heartbeat.release.set()
    await deliveries.heartbeat_started.wait()
    internal.release.set()
    await internal.completed.wait()

    assert deliveries.results == []
    deliveries.heartbeat_release.set()
    with pytest.raises(StaleTeamDeliveryClaimError):
        await task

    assert deliveries.results == []
    assert deliveries.release_calls == []
    assert queue.acked == []


@async_test
async def test_heartbeat_loss_stops_new_service_calls_after_active_query() -> None:
    target = _target()
    heartbeat = HeartbeatGate()
    queries = BlockingRunQueries()
    internal = FakeInternalSubmissions([_receipt(target)])
    deliveries = FakeDeliveries(_claimed(target))
    deliveries.heartbeat_error = StaleTeamDeliveryClaimError()
    queue = FakeQueue()
    runner, _, _ = _runner(
        bootstrap=FakeBootstrap([target]),
        deliveries=deliveries,
        queue=queue,
        teams=FakeTeams(_active_member()),
        run_queries=queries,
        internal_submissions=internal,
        heartbeat_wait=heartbeat,
    )
    delivery = QueueDelivery("1-0", target.outbox_id, 1)

    task = asyncio.create_task(runner.run_delivery(delivery))
    await queries.started.wait()
    await heartbeat.waiting.wait()
    heartbeat.release.set()
    while not deliveries.heartbeat_calls:
        await asyncio.sleep(0)
    queries.release.set()

    with pytest.raises(StaleTeamDeliveryClaimError):
        await task

    assert internal.calls == []
    assert deliveries.results == []
    assert queue.acked == []


@async_test
async def test_result_commit_failure_is_not_acked_and_duplicate_receipt_converges() -> None:
    target = _target()
    delivery = QueueDelivery("1-0", target.outbox_id, 1)
    receipt = _receipt(target)

    first_deliveries = FakeDeliveries(_claimed(target))
    first_deliveries.commit_error = RuntimeError("result commit unavailable")
    first_queue = FakeQueue()
    first, _, _ = _runner(
        bootstrap=FakeBootstrap([target]),
        deliveries=first_deliveries,
        queue=first_queue,
        teams=FakeTeams(_active_member()),
        run_queries=FakeRunQueries([None]),
        internal_submissions=FakeInternalSubmissions([receipt]),
    )

    with pytest.raises(RuntimeError, match="result commit unavailable"):
        await first.run_delivery(delivery)
    assert first_queue.acked == []

    takeover_deliveries = FakeDeliveries(_claimed(target, fencing_token=2))
    takeover_queue = FakeQueue()
    takeover_internal = FakeInternalSubmissions(
        [],
        applied=InternalRunInputReceipt(
            target.delivery.delivery_id,
            "session_2",
            "internal_start",
            "run_2",
            1,
        ),
    )
    takeover, _, _ = _runner(
        bootstrap=FakeBootstrap([target]),
        deliveries=takeover_deliveries,
        queue=takeover_queue,
        teams=FakeTeams(_active_member()),
        run_queries=FakeRunQueries([]),
        internal_submissions=takeover_internal,
    )

    assert await takeover.run_delivery(delivery) is True

    assert takeover_internal.calls == []
    assert len(takeover_internal.applied_calls) == 1
    assert takeover_deliveries.results[0].result_kind == "internal_start"
    assert takeover_queue.acked == [delivery]


@async_test
async def test_stale_result_fence_never_acks_or_releases() -> None:
    target = _target()
    deliveries = FakeDeliveries(_claimed(target))
    deliveries.commit_error = StaleTeamDeliveryClaimError()
    queue = FakeQueue()
    runner, _, _ = _runner(
        bootstrap=FakeBootstrap([target]),
        deliveries=deliveries,
        queue=queue,
        teams=FakeTeams(_active_member()),
        run_queries=FakeRunQueries([None]),
        internal_submissions=FakeInternalSubmissions([_receipt(target)]),
    )

    with pytest.raises(StaleTeamDeliveryClaimError):
        await runner.run_delivery(QueueDelivery("1-0", target.outbox_id, 1))

    assert deliveries.release_calls == []
    assert queue.acked == []


@async_test
async def test_queue_ack_happens_only_after_result_commit() -> None:
    target = _target()
    trace: list[str] = []
    deliveries = FakeDeliveries(_claimed(target), trace=trace)
    queue = FakeQueue(trace)
    runner, _, _ = _runner(
        bootstrap=FakeBootstrap([target]),
        deliveries=deliveries,
        queue=queue,
        teams=FakeTeams(_active_member()),
        run_queries=FakeRunQueries([None]),
        internal_submissions=FakeInternalSubmissions([_receipt(target)]),
    )

    assert await runner.run_delivery(QueueDelivery("1-0", target.outbox_id, 1))

    assert trace.index("postgres.commit_result") < trace.index("queue.ack")
