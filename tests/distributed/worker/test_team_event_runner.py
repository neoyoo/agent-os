from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import QueueDelivery, RequestScope
from agentos.distributed.worker.team_events import TeamEventDeliveryRunner
from agentos.multi.team_delivery_types import TeamDeliveryResult
from agentos.multi.team_event_types import (
    TeamDeliveryAppliedEvent,
    TeamEventEnvelope,
    TeamEventReplayItem,
    TeamEventTarget,
)
from agentos.runtime.run_state import RunStatus
from tests.planning._async import async_test


NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
OUTBOX_ID = "team_outbox_" + "1" * 64
DELIVERY = QueueDelivery("1-0", OUTBOX_ID, 1)
EVENT = TeamEventEnvelope(
    tenant_id="tenant_1",
    team_id="team_1",
    event_sequence=1,
    event=TeamDeliveryAppliedEvent(
        "team_delivery_" + "2" * 64,
        TeamDeliveryResult(
            "internal_start",
            "run_1",
            1,
            RunStatus.QUEUED,
        ),
    ),
    occurred_at=NOW,
)
TARGET = TeamEventTarget("tenant_1", OUTBOX_ID, EVENT)


class FakeBootstrap:
    def __init__(self, target: TeamEventTarget | None) -> None:
        self.target = target
        self.calls: list[str] = []

    async def resolve_event(self, *, outbox_id: str) -> TeamEventTarget | None:
        self.calls.append(outbox_id)
        return self.target


class FakeReplay:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.calls: list[tuple[RequestScope, TeamEventEnvelope]] = []
        self.error: BaseException | None = None

    async def append(
        self,
        *,
        scope: RequestScope,
        event: TeamEventEnvelope,
    ) -> TeamEventReplayItem:
        self.trace.append("redis.append")
        self.calls.append((scope, event))
        if self.error is not None:
            raise self.error
        return TeamEventReplayItem("1-0", event)


class FakeQueue:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.acked: list[QueueDelivery] = []

    async def ack(self, *, topic: str, delivery: QueueDelivery) -> None:
        assert topic == "team-events"
        self.trace.append("queue.ack")
        self.acked.append(delivery)


def _runner(
    bootstrap: FakeBootstrap,
    replay: FakeReplay,
    queue: FakeQueue,
) -> TeamEventDeliveryRunner:
    return TeamEventDeliveryRunner(
        bootstrap=bootstrap,
        replay=replay,
        queue=queue,
        principal_id="team_event_service",
        topic="team-events",
        claim_ttl=timedelta(minutes=1),
        heartbeat_interval=timedelta(seconds=10),
    )


@async_test
async def test_team_event_runner_appends_authoritative_event_before_ack() -> None:
    trace: list[str] = []
    bootstrap = FakeBootstrap(TARGET)
    replay = FakeReplay(trace)
    queue = FakeQueue(trace)

    assert await _runner(bootstrap, replay, queue).run_delivery(DELIVERY) is True

    assert bootstrap.calls == [OUTBOX_ID]
    assert replay.calls == [(RequestScope("tenant_1", "team_event_service"), EVENT)]
    assert queue.acked == [DELIVERY]
    assert trace == ["redis.append", "queue.ack"]


@async_test
async def test_team_event_runner_does_not_ack_unknown_outbox() -> None:
    trace: list[str] = []
    bootstrap = FakeBootstrap(None)
    replay = FakeReplay(trace)
    queue = FakeQueue(trace)

    assert await _runner(bootstrap, replay, queue).run_delivery(DELIVERY) is False

    assert replay.calls == []
    assert queue.acked == []


@async_test
async def test_team_event_runner_does_not_ack_failed_replay_append() -> None:
    trace: list[str] = []
    replay = FakeReplay(trace)
    replay.error = DeliveryUnavailableError()
    queue = FakeQueue(trace)

    with pytest.raises(DeliveryUnavailableError):
        await _runner(FakeBootstrap(TARGET), replay, queue).run_delivery(DELIVERY)

    assert queue.acked == []

