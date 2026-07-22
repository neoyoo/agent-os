from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.models import QueueDelivery, RequestScope
from agentos.distributed.worker.team import TeamDeliveryRunner
from agentos.multi.team_delivery_types import (
    ClaimedTeamDelivery,
    TeamDelivery,
    TeamDeliveryClaim,
    TeamDeliveryResult,
    TeamDeliveryTarget,
)
from agentos.multi.team_identity import (
    team_delivery_id,
    team_delivery_source_digest,
    team_message_id,
    team_message_request_digest,
    team_outbox_id,
)
from agentos.multi.team_types import TeamMemberRecord, TeamMessage, TeamRecipient
from agentos.runtime.run_state import RunStatus
from tests.planning._async import async_test


NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
TENANT_SCOPE = RequestScope("tenant_1", "sender_service")
DELIVERY = QueueDelivery("1-0", "team_outbox_placeholder", 1)


def _target(
    *,
    state: str = "pending",
    correlation_id: str | None = None,
) -> TeamDeliveryTarget:
    recipient = TeamRecipient("agent_2", "session_2")
    message_id = team_message_id(
        scope=TENANT_SCOPE,
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
    )
    request_sha256 = team_message_request_digest(
        scope=TENANT_SCOPE,
        team_id="team_1",
        message_id=message_id,
        sender_agent_id="agent_1",
        message_kind="instruction",
        content="review the current plan",
        correlation_id=correlation_id,
        addressing_kind="direct",
        addressed_agent_id="agent_2",
    )
    message = TeamMessage(
        message_id=message_id,
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="instruction",
        content="review the current plan",
        correlation_id=correlation_id,
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        recipient_snapshot=(recipient,),
        request_sha256=request_sha256,
        created_at=NOW,
    )
    delivery_id = team_delivery_id(
        scope=TENANT_SCOPE,
        team_id="team_1",
        message_id=message_id,
        recipient_agent_id=recipient.recipient_agent_id,
        target_session_id=recipient.target_session_id,
    )
    source_sha256 = team_delivery_source_digest(
        scope=TENANT_SCOPE,
        team_id="team_1",
        message_id=message_id,
        sender_agent_id="agent_1",
        message_kind="instruction",
        content="review the current plan",
        correlation_id=correlation_id,
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        recipient_agent_id=recipient.recipient_agent_id,
        target_session_id=recipient.target_session_id,
    )
    terminal = state == "applied"
    delivery = TeamDelivery(
        delivery_id=delivery_id,
        team_id="team_1",
        message_id=message_id,
        recipient_agent_id=recipient.recipient_agent_id,
        target_session_id=recipient.target_session_id,
        state=state,  # type: ignore[arg-type]
        fencing_token=1 if terminal else 0,
        source_sha256=source_sha256,
        created_at=NOW,
        updated_at=NOW,
        result_kind="internal_start" if terminal else None,
        observed_run_id="run_1" if terminal else None,
        observed_aggregate_version=1 if terminal else None,
        observed_run_status=RunStatus.QUEUED if terminal else None,
    )
    outbox_id = team_outbox_id(
        scope=TENANT_SCOPE,
        delivery_id=delivery_id,
        outbox_kind="delivery_ready",
    )
    return TeamDeliveryTarget(
        tenant_id=TENANT_SCOPE.tenant_id,
        outbox_id=outbox_id,
        delivery=delivery,
        message=message,
    )


def _claimed(
    target: TeamDeliveryTarget,
    *,
    claim_id: str = "team_worker_1:1-0",
    fencing_token: int = 1,
) -> ClaimedTeamDelivery:
    expires_at = NOW + timedelta(seconds=30)
    delivery = replace(
        target.delivery,
        state="claimed",
        fencing_token=fencing_token,
        claim_id=claim_id,
        claim_expires_at=expires_at,
    )
    claimed_target = replace(target, delivery=delivery)
    return ClaimedTeamDelivery(
        target=claimed_target,
        claim=TeamDeliveryClaim(
            tenant_id=target.tenant_id,
            team_id=delivery.team_id,
            delivery_id=delivery.delivery_id,
            claim_id=claim_id,
            fencing_token=fencing_token,
            expires_at=expires_at,
        ),
    )


class FakeBootstrap:
    def __init__(self, targets: list[TeamDeliveryTarget | None]) -> None:
        self.targets = targets
        self.calls: list[str] = []

    async def resolve_delivery(self, *, outbox_id: str) -> TeamDeliveryTarget | None:
        self.calls.append(outbox_id)
        return self.targets.pop(0)


class FakeDeliveries:
    def __init__(
        self,
        claimed: ClaimedTeamDelivery | None = None,
        *,
        trace: list[str] | None = None,
    ) -> None:
        self.claimed = claimed
        self.trace = trace if trace is not None else []
        self.claim_calls: list[tuple[RequestScope, str, str, timedelta]] = []
        self.heartbeat_calls: list[TeamDeliveryClaim] = []
        self.release_calls: list[TeamDeliveryClaim] = []
        self.results: list[TeamDeliveryResult] = []
        self.heartbeat_error: BaseException | None = None
        self.commit_error: BaseException | None = None

    async def claim_pending(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        claim_id: str,
        ttl: timedelta,
    ) -> ClaimedTeamDelivery | None:
        self.trace.append("postgres.claim")
        self.claim_calls.append((scope, outbox_id, claim_id, ttl))
        if self.claimed is not None and self.claimed.claim.claim_id != claim_id:
            claim = replace(self.claimed.claim, claim_id=claim_id)
            delivery = replace(
                self.claimed.target.delivery,
                claim_id=claim_id,
            )
            self.claimed = ClaimedTeamDelivery(
                target=replace(self.claimed.target, delivery=delivery),
                claim=claim,
            )
        return self.claimed

    async def heartbeat(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
        ttl: timedelta,
    ) -> TeamDeliveryClaim:
        self.trace.append("postgres.heartbeat")
        self.heartbeat_calls.append(claim)
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return replace(claim, expires_at=claim.expires_at + ttl)

    async def release(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
    ) -> None:
        self.trace.append("postgres.release")
        self.release_calls.append(claim)

    async def commit_result(
        self,
        *,
        scope: RequestScope,
        claim: TeamDeliveryClaim,
        result: TeamDeliveryResult,
    ) -> TeamDelivery:
        self.trace.append("postgres.commit_result")
        self.results.append(result)
        if self.commit_error is not None:
            raise self.commit_error
        assert self.claimed is not None
        state = "applied" if result.result_kind in {"internal_start", "wakeup"} else "rejected"
        return replace(
            self.claimed.target.delivery,
            state=state,
            claim_id=None,
            claim_expires_at=None,
            result_kind=result.result_kind,
            observed_run_id=result.observed_run_id,
            observed_aggregate_version=result.observed_aggregate_version,
            observed_run_status=result.observed_run_status,
        )


class FakeQueue:
    def __init__(self, trace: list[str] | None = None) -> None:
        self.trace = trace if trace is not None else []
        self.acked: list[QueueDelivery] = []

    async def ack(self, *, topic: str, delivery: QueueDelivery) -> None:
        assert topic == "team-deliveries"
        self.trace.append("queue.ack")
        self.acked.append(delivery)


class FakeTeams:
    def __init__(self, member: TeamMemberRecord | None) -> None:
        self.member = member
        self.calls: list[tuple[RequestScope, str, str]] = []

    async def get_member(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        recipient_agent_id: str,
    ) -> TeamMemberRecord | None:
        self.calls.append((scope, team_id, recipient_agent_id))
        return self.member


def _active_member() -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_2",
        role="worker",
        target_session_id="session_2",
        created_at=NOW,
    )


def _runner(
    *,
    bootstrap: FakeBootstrap,
    deliveries: FakeDeliveries | None = None,
    queue: FakeQueue | None = None,
    teams: object | None = None,
    run_queries: object | None = None,
    internal_submissions: object | None = None,
    claim_ttl: timedelta = timedelta(seconds=30),
    heartbeat_interval: timedelta = timedelta(seconds=10),
    heartbeat_wait=asyncio.sleep,
) -> tuple[TeamDeliveryRunner, FakeDeliveries, FakeQueue]:
    selected_deliveries = deliveries or FakeDeliveries()
    selected_queue = queue or FakeQueue()
    return (
        TeamDeliveryRunner(
            bootstrap=bootstrap,
            deliveries=selected_deliveries,
            queue=selected_queue,
            teams=teams if teams is not None else object(),
            run_queries=run_queries if run_queries is not None else object(),
            internal_submissions=(
                internal_submissions if internal_submissions is not None else object()
            ),
            worker_id="team_worker_1",
            principal_id="team_delivery_service",
            topic="team-deliveries",
            claim_ttl=claim_ttl,
            heartbeat_interval=heartbeat_interval,
            heartbeat_wait=heartbeat_wait,
        ),
        selected_deliveries,
        selected_queue,
    )


def test_team_runner_requires_heartbeat_no_slower_than_one_third_ttl() -> None:
    bootstrap = FakeBootstrap([])

    runner, _, _ = _runner(
        bootstrap=bootstrap,
        claim_ttl=timedelta(seconds=30),
        heartbeat_interval=timedelta(seconds=10),
    )
    assert runner.heartbeat_interval == timedelta(seconds=10)

    with pytest.raises(ValueError, match="one third"):
        _runner(
            bootstrap=bootstrap,
            claim_ttl=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10, microseconds=1),
        )


@async_test
async def test_team_runner_resolves_opaque_outbox_before_acknowledging_terminal() -> None:
    target = _target(state="applied")
    delivery = QueueDelivery("1-0", target.outbox_id, 1)
    bootstrap = FakeBootstrap([target])
    runner, deliveries, queue = _runner(bootstrap=bootstrap)

    assert await runner.run_delivery(delivery) is True

    assert bootstrap.calls == [target.outbox_id]
    assert deliveries.claim_calls == []
    assert queue.acked == [delivery]


@async_test
async def test_team_runner_does_not_ack_unknown_outbox() -> None:
    bootstrap = FakeBootstrap([None])
    runner, deliveries, queue = _runner(bootstrap=bootstrap)

    assert await runner.run_delivery(DELIVERY) is False

    assert bootstrap.calls == [DELIVERY.outbox_id]
    assert deliveries.claim_calls == []
    assert queue.acked == []


@async_test
async def test_team_runner_refreshes_truth_when_claim_loses_a_race() -> None:
    pending = _target()
    terminal = _target(state="applied")
    delivery = QueueDelivery("1-0", pending.outbox_id, 1)
    bootstrap = FakeBootstrap([pending, terminal])
    runner, deliveries, queue = _runner(bootstrap=bootstrap)

    assert await runner.run_delivery(delivery) is True

    scope, outbox_id, claim_id, ttl = deliveries.claim_calls[0]
    assert scope == RequestScope("tenant_1", "team_delivery_service")
    assert outbox_id == pending.outbox_id
    assert claim_id.startswith("team_claim_")
    assert len(claim_id) == len("team_claim_") + 64
    assert ttl == timedelta(seconds=30)
    assert bootstrap.calls == [pending.outbox_id, pending.outbox_id]
    assert queue.acked == [delivery]


@async_test
async def test_team_runner_leaves_pending_when_claim_race_is_not_terminal() -> None:
    pending = _target()
    delivery = QueueDelivery("1-0", pending.outbox_id, 1)
    bootstrap = FakeBootstrap([pending, pending])
    runner, _, queue = _runner(bootstrap=bootstrap)

    assert await runner.run_delivery(delivery) is False

    assert queue.acked == []
