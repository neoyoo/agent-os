import asyncio
from datetime import UTC, datetime

import pytest

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import RequestScope
from agentos.distributed.redis._team_event_codec import encode_team_envelope
from agentos.distributed.redis.team_replay import RedisTeamEventReplayAdapter
from agentos.multi.team_delivery_types import TeamDeliveryResult
from agentos.multi.team_event_types import (
    TeamDeliveryAppliedEvent,
    TeamDeliveryRejectedEvent,
    TeamEventEnvelope,
    TeamEventReplayBatch,
    TeamStreamGap,
)
from agentos.runtime.run_state import RunStatus

from _fake_redis import FakeAsyncRedis


SCOPE = RequestScope("tenant_1", "user_1")
NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
DELIVERY_ID = "team_delivery_" + "3" * 64


def _event(
    sequence: int,
    result_kind: str = "internal_start",
) -> TeamEventEnvelope:
    if result_kind == "internal_start":
        result = TeamDeliveryResult(
            result_kind="internal_start",
            observed_run_id="run_1",
            observed_aggregate_version=1,
            observed_run_status=RunStatus.QUEUED,
        )
        event = TeamDeliveryAppliedEvent(DELIVERY_ID, result)
    elif result_kind == "wakeup":
        result = TeamDeliveryResult(
            result_kind="wakeup",
            observed_run_id="run_1",
            observed_aggregate_version=2,
            observed_run_status=RunStatus.WAITING,
        )
        event = TeamDeliveryAppliedEvent(DELIVERY_ID, result)
    elif result_kind == "rejected_nonterminal":
        result = TeamDeliveryResult(
            result_kind="rejected_nonterminal",
            observed_run_id="run_1",
            observed_aggregate_version=3,
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


def test_team_replay_round_trips_typed_events_and_reports_trimmed_gap() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(
            client=redis,
            key_prefix="test",
            max_events=2,
        )
        first = await replay.append(scope=SCOPE, event=_event(1))
        second = await replay.append(scope=SCOPE, event=_event(2, "wakeup"))
        third = await replay.append(
            scope=SCOPE,
            event=_event(3, "rejected_nonterminal"),
        )

        batch = await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=second.cursor,
            limit=10,
        )
        assert batch == TeamEventReplayBatch((third,), third.cursor)

        gap = await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=first.cursor,
            limit=10,
        )
        assert gap == TeamStreamGap(
            tenant_id="tenant_1",
            team_id="team_1",
            requested_cursor=first.cursor,
            oldest_available_cursor=second.cursor,
            reason="trimmed",
        )

        fields = redis.stream_fields("test:team-events:tenant_1:team_1")
        assert fields
        assert set(fields[-1]) == {
            "tenant_id",
            "team_id",
            "event_sequence",
            "event_kind",
            "delivery_id",
            "result_kind",
            "observed_run_id",
            "observed_aggregate_version",
            "observed_run_status",
            "occurred_at",
        }

    asyncio.run(scenario())


def test_team_replay_append_is_idempotent_by_postgres_event_sequence() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis)
        event = _event(7)

        first = await replay.append(scope=SCOPE, event=event)
        duplicate = await replay.append(scope=SCOPE, event=event)

        assert duplicate == first
        assert len(redis.streams["agentos:team-events:tenant_1:team_1"]) == 1
        with pytest.raises(DeliveryUnavailableError):
            await replay.append(scope=SCOPE, event=_event(7, "wakeup"))

    asyncio.run(scenario())


def test_team_replay_orders_delayed_events_by_postgres_sequence() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis, max_events=3)

        second = await replay.append(scope=SCOPE, event=_event(2, "wakeup"))
        first = await replay.append(scope=SCOPE, event=_event(1))

        assert first.cursor == "1-0"
        assert second.cursor == "2-0"
        batch = await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=None,
            limit=3,
        )
        assert isinstance(batch, TeamEventReplayBatch)
        assert tuple(item.event.event_sequence for item in batch.items) == (1, 2)

    asyncio.run(scenario())


def test_team_replay_does_not_resurrect_an_event_outside_retention() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis, max_events=2)

        first = await replay.append(scope=SCOPE, event=_event(1))
        second = await replay.append(scope=SCOPE, event=_event(2, "wakeup"))
        third = await replay.append(
            scope=SCOPE,
            event=_event(3, "rejected_nonterminal"),
        )
        stale_receipt = await replay.append(scope=SCOPE, event=_event(1))

        assert stale_receipt == first
        batch = await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=None,
            limit=2,
        )
        assert batch == TeamEventReplayBatch((second, third), third.cursor)

    asyncio.run(scenario())


def test_team_replay_invalidates_an_oversized_preexisting_stream() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        stream = "agentos:team-events:tenant_1:team_1"
        for index in range(3):
            await redis.xadd(stream, {"legacy": str(index)})
        replay = RedisTeamEventReplayAdapter(client=redis, max_events=2)

        current = await replay.append(
            scope=SCOPE,
            event=_event(4, "rejected_binding_revoked"),
        )

        assert current.cursor == "4-0"
        assert redis.streams[stream] == [(current.cursor, redis.stream_fields(stream)[0])]
        script = redis.eval_calls[-1][0]
        assert "agentos:team-replay:ensure-event:v2" in script
        assert "redis.call('XLEN', KEYS[1])" in script
        assert "redis.call('UNLINK', KEYS[1])" in script
        assert "'COUNT', ARGV[2]" in script
        assert "redis.call('XRANGE', KEYS[1], '-', '+')" not in script

    asyncio.run(scenario())


def test_team_replay_preserves_the_latest_canonical_oversized_window() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        stream = "agentos:team-events:tenant_1:team_1"
        for sequence in range(1, 4):
            await redis.xadd(
                stream,
                encode_team_envelope(_event(sequence)),
                id=f"{sequence}-0",
            )
        replay = RedisTeamEventReplayAdapter(client=redis, max_events=2)

        fourth = await replay.append(
            scope=SCOPE,
            event=_event(4, "rejected_binding_revoked"),
        )

        batch = await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=None,
            limit=2,
        )
        assert isinstance(batch, TeamEventReplayBatch)
        assert tuple(item.event.event_sequence for item in batch.items) == (3, 4)
        assert batch.next_cursor == fourth.cursor

    asyncio.run(scenario())


def test_team_replay_rejects_retention_above_the_sdk_hard_max() -> None:
    redis = FakeAsyncRedis()

    with pytest.raises(ValueError, match="max_events"):
        RedisTeamEventReplayAdapter(client=redis, max_events=1_001)

    assert redis.eval_calls == []


def test_team_replay_round_trips_every_allowlisted_result_kind() -> None:
    async def scenario() -> None:
        replay = RedisTeamEventReplayAdapter(client=FakeAsyncRedis())
        envelopes = tuple(
            _event(index, result_kind)
            for index, result_kind in enumerate(
                (
                    "internal_start",
                    "wakeup",
                    "rejected_binding_revoked",
                    "rejected_nonterminal",
                ),
                start=1,
            )
        )
        for envelope in envelopes:
            await replay.append(scope=SCOPE, event=envelope)

        batch = await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=None,
            limit=10,
        )

        assert isinstance(batch, TeamEventReplayBatch)
        assert tuple(item.event for item in batch.items) == envelopes

    asyncio.run(scenario())


def test_team_replay_captures_high_water_without_consuming() -> None:
    async def scenario() -> None:
        replay = RedisTeamEventReplayAdapter(client=FakeAsyncRedis())

        assert await replay.high_water(scope=SCOPE, team_id="team_1") is None
        first = await replay.append(scope=SCOPE, event=_event(1))
        second = await replay.append(scope=SCOPE, event=_event(2, "wakeup"))

        assert await replay.high_water(scope=SCOPE, team_id="team_1") == second.cursor
        batch = await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=first.cursor,
            limit=10,
        )
        assert batch == TeamEventReplayBatch((second,), second.cursor)

    asyncio.run(scenario())


def test_team_replay_reports_unavailable_gap_after_stream_loss() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis)
        first = await replay.append(scope=SCOPE, event=_event(1))
        redis.streams.clear()

        assert await replay.replay(
            scope=SCOPE,
            team_id="team_1",
            after=first.cursor,
            limit=10,
        ) == TeamStreamGap(
            tenant_id="tenant_1",
            team_id="team_1",
            requested_cursor=first.cursor,
            oldest_available_cursor=None,
            reason="unavailable",
        )

    asyncio.run(scenario())


def test_team_replay_rejects_cross_tenant_events_and_backend_data() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis)

        with pytest.raises(ValueError, match="tenant"):
            await replay.append(
                scope=RequestScope("tenant_2", "user_2"),
                event=_event(1),
            )
        assert redis.streams == {}

        await replay.append(scope=SCOPE, event=_event(1))
        source = redis.streams["agentos:team-events:tenant_1:team_1"]
        redis.streams["agentos:team-events:tenant_2:team_1"] = list(source)

        with pytest.raises(DeliveryUnavailableError):
            await replay.replay(
                scope=RequestScope("tenant_2", "user_2"),
                team_id="team_1",
                after=None,
                limit=10,
            )
        with pytest.raises(DeliveryUnavailableError):
            await replay.high_water(
                scope=RequestScope("tenant_2", "user_2"),
                team_id="team_1",
            )

    asyncio.run(scenario())


def test_team_replay_rejects_untrusted_scope_before_redis_io() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis)

        with pytest.raises(TypeError, match="RequestScope"):
            await replay.append(scope=object(), event=_event(1))  # type: ignore[arg-type]
        assert redis.streams == {}

    asyncio.run(scenario())


def test_team_replay_rejects_malformed_redis_cursor() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis)
        await replay.append(scope=SCOPE, event=_event(1))
        stream = redis.streams["agentos:team-events:tenant_1:team_1"]
        stream[0] = ("not-a-redis-cursor", stream[0][1])

        with pytest.raises(DeliveryUnavailableError):
            await replay.replay(
                scope=SCOPE,
                team_id="team_1",
                after=None,
                limit=10,
            )
        with pytest.raises(DeliveryUnavailableError):
            await replay.high_water(scope=SCOPE, team_id="team_1")

    asyncio.run(scenario())


def test_team_replay_rejects_cursor_that_disagrees_with_event_sequence() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisTeamEventReplayAdapter(client=redis)
        await replay.append(scope=SCOPE, event=_event(1))
        stream = redis.streams["agentos:team-events:tenant_1:team_1"]
        stream[0] = ("999-0", stream[0][1])

        with pytest.raises(DeliveryUnavailableError):
            await replay.replay(
                scope=SCOPE,
                team_id="team_1",
                after=None,
                limit=10,
            )
        with pytest.raises(DeliveryUnavailableError):
            await replay.high_water(scope=SCOPE, team_id="team_1")

    asyncio.run(scenario())
