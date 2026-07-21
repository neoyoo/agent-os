import asyncio
from datetime import UTC, datetime

import pytest

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import (
    LiveContentDelta,
    LiveContextLoaded,
    LiveFinalResult,
    LivePlanUpdated,
    LiveSkillLoaded,
    LiveStatusUpdate,
    LiveToolStatus,
    LiveTurnCancelled,
    LiveTurnCompleted,
    LiveTurnFailed,
    LiveTurnStarted,
    LiveTurnWaiting,
    ReplayBatch,
    RequestScope,
    RunEventEnvelope,
    StreamGap,
)
from agentos.distributed.redis.replay import RedisEventReplayAdapter

from _fake_redis import FakeAsyncRedis


SCOPE = RequestScope("tenant_1", "user_1")
NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


class RepeatedTimeoutRedis(FakeAsyncRedis):
    def __init__(self) -> None:
        super().__init__()
        self.empty_read_count = 0

    async def xread(self, *args: object, **kwargs: object) -> object:
        if self.empty_read_count < 1_100:
            self.empty_read_count += 1
            await asyncio.sleep(0)
            return []
        return await super().xread(*args, **kwargs)


class TrimAfterOldestReadRedis(FakeAsyncRedis):
    def __init__(self) -> None:
        super().__init__()
        self._trimmed = False

    async def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        *,
        count: int | None = None,
    ) -> object:
        rows = await super().xrange(name, min=min, max=max, count=count)
        if not self._trimmed and min == "-" and count == 1:
            self._trimmed = True
            self.streams[name] = self.streams[name][2:]
        return rows

    async def before_atomic_replay(self, name: str) -> None:
        if not self._trimmed:
            self._trimmed = True
            self.streams[name] = self.streams[name][2:]


def _event(sequence: int, text: str) -> RunEventEnvelope:
    return RunEventEnvelope(
        tenant_id=SCOPE.tenant_id,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        execution_attempt=1,
        event_sequence=sequence,
        event=LiveContentDelta(sequence, text),
        occurred_at=NOW,
    )


def test_event_replay_round_trips_history_and_detects_trimmed_cursor_gap() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(
            client=redis,
            key_prefix="test",
            max_events=2,
        )
        first = await replay.append(scope=SCOPE, event=_event(0, "first"))
        second = await replay.append(scope=SCOPE, event=_event(1, "second"))
        third = await replay.append(scope=SCOPE, event=_event(2, "third"))

        batch = await replay.replay(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=second.cursor,
            limit=10,
        )
        assert batch == ReplayBatch((third,), third.cursor)

        gap = await replay.replay(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=first.cursor,
            limit=10,
        )
        assert gap == StreamGap(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            requested_cursor=first.cursor,
            oldest_available_cursor=second.cursor,
            reason="trimmed",
        )

    asyncio.run(scenario())


def test_event_replay_captures_current_high_water_without_consuming() -> None:
    async def scenario() -> None:
        replay = RedisEventReplayAdapter(client=FakeAsyncRedis())

        assert await replay.high_water(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
        ) is None
        first = await replay.append(scope=SCOPE, event=_event(0, "first"))
        second = await replay.append(scope=SCOPE, event=_event(1, "second"))

        assert await replay.high_water(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
        ) == second.cursor
        batch = await replay.replay(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=first.cursor,
            limit=10,
        )
        assert batch == ReplayBatch((second,), second.cursor)

    asyncio.run(scenario())


def test_event_replay_detects_trim_between_oldest_check_and_range() -> None:
    async def scenario() -> None:
        redis = TrimAfterOldestReadRedis()
        replay = RedisEventReplayAdapter(client=redis, max_events=10)
        first = await replay.append(scope=SCOPE, event=_event(0, "first"))
        await replay.append(scope=SCOPE, event=_event(1, "second"))
        third = await replay.append(scope=SCOPE, event=_event(2, "third"))

        result = await replay.replay(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=first.cursor,
            limit=10,
        )

        assert result == StreamGap(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            requested_cursor=first.cursor,
            oldest_available_cursor=third.cursor,
            reason="trimmed",
        )

    asyncio.run(scenario())


def test_event_follow_replays_then_tails_new_events() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis, block_ms=30_000)
        first = await replay.append(scope=SCOPE, event=_event(0, "first"))
        subscription = replay.follow(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=None,
        )

        assert await anext(subscription) == first
        waiting = asyncio.create_task(anext(subscription))
        await redis.read_started.wait()
        second = await replay.append(scope=SCOPE, event=_event(1, "second"))
        assert await waiting == second
        await subscription.aclose()

    asyncio.run(scenario())


def test_event_follow_reports_gap_when_trim_happens_during_blocking_read() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(
            client=redis,
            max_events=2,
            block_ms=30_000,
        )
        first = await replay.append(scope=SCOPE, event=_event(0, "first"))
        subscription = replay.follow(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=first.cursor,
        )

        waiting = asyncio.create_task(anext(subscription))
        await redis.read_started.wait()
        second = await replay.append(scope=SCOPE, event=_event(1, "second"))
        oldest = await replay.append(scope=SCOPE, event=_event(2, "third"))
        await replay.append(scope=SCOPE, event=_event(3, "fourth"))

        assert await waiting == StreamGap(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            requested_cursor=first.cursor,
            oldest_available_cursor=oldest.cursor,
            reason="trimmed",
        )
        assert second.cursor not in {
            cursor
            for cursor, _ in redis.streams[
                "agentos:events:tenant_1:session_1:run_1"
            ]
        }
        await subscription.aclose()

    asyncio.run(scenario())


def test_event_replay_round_trips_every_allowlisted_live_event() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis, max_events=32)
        live_events = (
            LiveTurnStarted(),
            LiveStatusUpdate("provider", "working"),
            LiveContextLoaded("memory"),
            LiveSkillLoaded("review"),
            LivePlanUpdated("inspect", "updated"),
            LiveContentDelta(0, "answer"),
            LiveToolStatus("lookup", "call_1", "started"),
            LiveToolStatus("lookup", "call_1", "completed"),
            LiveToolStatus("lookup", "call_1", "failed"),
            LiveFinalResult("done"),
            LiveTurnCompleted(),
            LiveTurnWaiting("human_input", "wait_1"),
            LiveTurnWaiting("timer", "wait_2", NOW),
            LiveTurnFailed(),
            LiveTurnCancelled(),
        )
        envelopes = tuple(
            RunEventEnvelope(
                tenant_id="tenant_1",
                session_id="session_1",
                run_id="run_1",
                turn_id="turn_1",
                execution_attempt=1,
                event_sequence=index,
                event=event,
                occurred_at=NOW,
            )
            for index, event in enumerate(live_events)
        )
        for envelope in envelopes:
            await replay.append(scope=SCOPE, event=envelope)

        batch = await replay.replay(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=None,
            limit=32,
        )

        assert isinstance(batch, ReplayBatch)
        assert tuple(item.event for item in batch.items) == envelopes
        script, numkeys, args = redis.eval_calls[-1]
        assert script.splitlines()[1] == "-- agentos:replay:snapshot:v1"
        assert (numkeys, len(args)) == (1, 3)

    asyncio.run(scenario())


def test_event_replay_rejects_cross_tenant_backend_data() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis)
        await replay.append(scope=SCOPE, event=_event(0, "first"))
        source = redis.streams["agentos:events:tenant_1:session_1:run_1"]
        redis.streams["agentos:events:tenant_2:session_1:run_1"] = list(source)

        with pytest.raises(DeliveryUnavailableError):
            await replay.replay(
                scope=RequestScope("tenant_2", "user_2"),
                session_id="session_1",
                run_id="run_1",
                after=None,
                limit=10,
            )

    asyncio.run(scenario())


def test_event_replay_close_cancels_tail_and_outages_are_typed() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis, block_ms=30_000)
        subscription = replay.follow(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=None,
        )
        blocked = asyncio.create_task(anext(subscription))
        await redis.read_started.wait()
        await replay.close()

        with pytest.raises(StopAsyncIteration):
            await blocked
        with pytest.raises(DistributedStoreClosedError):
            await replay.replay(
                scope=SCOPE,
                session_id="session_1",
                run_id="run_1",
                after=None,
                limit=1,
            )

        failing = FakeAsyncRedis()
        failing.fail = True
        unavailable = RedisEventReplayAdapter(client=failing)
        with pytest.raises(DeliveryUnavailableError) as caught:
            await unavailable.append(scope=SCOPE, event=_event(0, "first"))
        assert "secret" not in str(caught.value)

    asyncio.run(scenario())


def test_event_follow_handles_repeated_block_timeouts_without_recursion() -> None:
    async def scenario() -> None:
        redis = RepeatedTimeoutRedis()
        replay = RedisEventReplayAdapter(client=redis)
        first = await replay.append(scope=SCOPE, event=_event(0, "first"))
        subscription = replay.follow(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=None,
        )
        assert await anext(subscription) == first

        waiting = asyncio.create_task(anext(subscription))
        while redis.empty_read_count < 1_100 and not waiting.done():
            await asyncio.sleep(0)
        if not waiting.done():
            second = await replay.append(scope=SCOPE, event=_event(1, "second"))
            assert await waiting == second
        else:
            await waiting

    asyncio.run(scenario())
