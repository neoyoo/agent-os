import asyncio
from datetime import UTC, datetime

import pytest

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.redis.team_replay import RedisTeamEventReplayAdapter
from agentos.multi.team_delivery_types import (
    TeamDeliveryResult,
    TeamDeliveryResultKind,
)
from agentos.multi.team_event_types import (
    TeamDeliveryAppliedEvent,
    TeamDeliveryRejectedEvent,
    TeamEventEnvelope,
    TeamStreamGap,
)
from agentos.runtime.run_state import RunStatus

from _fake_redis import FakeAsyncRedis


SCOPE = RequestScope("tenant_1", "user_1")
NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
DELIVERY_ID = "team_delivery_" + "3" * 64


def _event(
    sequence: int,
    result_kind: TeamDeliveryResultKind = "internal_start",
) -> TeamEventEnvelope:
    if result_kind in {"internal_start", "wakeup"}:
        result = TeamDeliveryResult(
            result_kind=result_kind,
            observed_run_id="run_1",
            observed_aggregate_version=sequence,
            observed_run_status=(
                RunStatus.QUEUED
                if result_kind == "internal_start"
                else RunStatus.WAITING
            ),
        )
        event = TeamDeliveryAppliedEvent(DELIVERY_ID, result)
    elif result_kind == "rejected_nonterminal":
        result = TeamDeliveryResult(
            result_kind="rejected_nonterminal",
            observed_run_id="run_1",
            observed_aggregate_version=sequence,
            observed_run_status=RunStatus.RUNNING,
        )
        event = TeamDeliveryRejectedEvent(DELIVERY_ID, result)
    else:
        result = TeamDeliveryResult(
            result_kind="rejected_binding_revoked",
            observed_run_id=None,
            observed_aggregate_version=None,
            observed_run_status=None,
        )
        event = TeamDeliveryRejectedEvent(DELIVERY_ID, result)
    return TeamEventEnvelope(
        tenant_id=SCOPE.tenant_id,
        team_id="team_1",
        event_sequence=sequence,
        event=event,
        occurred_at=NOW,
    )


def test_team_follow_replays_then_tails_new_events() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis, block_ms=30_000)
        first = await replay.append(scope=SCOPE, event=_event(1))
        subscription = replay.follow(scope=SCOPE, team_id="team_1", after=None)

        assert await anext(subscription) == first
        waiting = asyncio.create_task(anext(subscription))
        await redis.read_started.wait()
        second = await replay.append(scope=SCOPE, event=_event(2, "wakeup"))
        assert await waiting == second
        await subscription.aclose()

    asyncio.run(scenario())


def test_team_follow_reports_gap_when_trim_happens_during_tail_read() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(
            client=redis,
            max_events=2,
            block_ms=30_000,
        )
        first = await replay.append(scope=SCOPE, event=_event(1))
        subscription = replay.follow(
            scope=SCOPE,
            team_id="team_1",
            after=first.cursor,
        )

        waiting = asyncio.create_task(anext(subscription))
        await redis.read_started.wait()
        await replay.append(scope=SCOPE, event=_event(2, "wakeup"))
        oldest = await replay.append(
            scope=SCOPE,
            event=_event(3, "rejected_nonterminal"),
        )
        await replay.append(
            scope=SCOPE,
            event=_event(4, "rejected_binding_revoked"),
        )

        assert await waiting == TeamStreamGap(
            tenant_id="tenant_1",
            team_id="team_1",
            requested_cursor=first.cursor,
            oldest_available_cursor=oldest.cursor,
            reason="trimmed",
        )
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)
        await subscription.aclose()

    asyncio.run(scenario())


def test_team_replay_close_cancels_blocked_tail_and_outages_are_typed() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis, block_ms=30_000)
        subscription = replay.follow(scope=SCOPE, team_id="team_1", after=None)
        blocked = asyncio.create_task(anext(subscription))
        await redis.read_started.wait()
        await replay.close()

        with pytest.raises(StopAsyncIteration):
            await blocked
        await replay.close()
        with pytest.raises(DistributedStoreClosedError):
            replay.follow(scope=SCOPE, team_id="team_1", after=None)

        failing = FakeAsyncRedis()
        failing.fail = True
        unavailable = RedisTeamEventReplayAdapter(client=failing)
        with pytest.raises(DeliveryUnavailableError) as caught:
            await unavailable.append(scope=SCOPE, event=_event(1))
        assert "secret" not in str(caught.value)

    asyncio.run(scenario())
