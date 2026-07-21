import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
    RunEventTooLargeError,
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
from agentos.distributed.run_event_limits import MAX_RUN_EVENT_JSON_BYTES
from agentos.distributed.postgres._state_records import run_read_model
from agentos.distributed.worker.runner import WorkerRunner
from agentos.runtime.run_state import RunStatus
from agentos.runtime.stream_events import FinalResult, TurnStreamCompleted

from _fake_redis import FakeAsyncRedis
from tests.distributed.worker._fakes import (
    DELIVERY,
    FakeAgent,
    FakeAgentFactory,
    FakeClaims,
    FakeLeases,
    FakeQueue,
    ScriptedStream,
    claimed_execution,
)


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


def _terminal() -> RunEventEnvelope:
    return RunEventEnvelope(
        tenant_id=SCOPE.tenant_id,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        execution_attempt=7,
        event_sequence=9_007_199_254_740_991,
        event=LiveTurnCompleted(),
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


def test_event_replay_rejects_oversized_event_before_redis_write() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis)

        with pytest.raises(RunEventTooLargeError):
            await replay.append(
                scope=SCOPE,
                event=_event(0, "x" * MAX_RUN_EVENT_JSON_BYTES),
            )

        assert redis.streams == {}

    asyncio.run(scenario())


def test_oversized_result_keeps_completed_read_model_and_replay_terminal() -> None:
    async def scenario() -> None:
        content = "结果" * MAX_RUN_EVENT_JSON_BYTES
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(
            trace,
            (FinalResult(content), TurnStreamCompleted(content)),
        )
        factory = FakeAgentFactory(trace, FakeAgent(trace, stream))
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis)
        runner = WorkerRunner(
            claims=claims,
            queue=queue,
            leases=leases,
            agent_factory=factory,
            event_sink=replay,
            worker_id="worker_1",
            topic="runs",
            claim_ttl=timedelta(minutes=1),
            lease_ttl=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert claims.outcome is not None
        assert claims.outcome.target.run.status is RunStatus.COMPLETED

        batch = await replay.replay(
            scope=claimed.target.scope,
            session_id=claimed.target.session_id,
            run_id=claimed.target.run.run_id,
            after=None,
            limit=10,
        )
        assert isinstance(batch, ReplayBatch)
        assert [item.event.event_kind for item in batch.items] == ["turn_completed"]

        read_model = run_read_model(
            {
                "tenant_id": claimed.target.scope.tenant_id,
                "session_id": claimed.target.session_id,
                "run_id": claimed.target.run.run_id,
                "status": RunStatus.COMPLETED.value,
                "wait_kind": None,
                "wait_handle": None,
                "wait_detail": None,
                "wait_not_before": None,
                "aggregate_version": claims.outcome.committed_version,
                "result_content": content,
            },
        )
        assert read_model.result is not None
        assert read_model.result.content == content
        assert queue.acked == [DELIVERY]

    asyncio.run(scenario())


def test_terminal_ensure_is_atomic_and_idempotent() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis)
        terminal = _terminal()

        first = await replay.ensure_terminal(scope=SCOPE, event=terminal)
        duplicate = await replay.ensure_terminal(scope=SCOPE, event=terminal)

        assert duplicate == first
        assert len(redis.streams["agentos:events:tenant_1:session_1:run_1"]) == 1
        script, numkeys, args = redis.eval_calls[-1]
        assert script.splitlines()[1] == "-- agentos:replay:ensure-terminal:v1"
        assert numkeys == 1
        assert args[1:3] == (
            str(terminal.execution_attempt),
            str(terminal.event_sequence),
        )

    asyncio.run(scenario())


def test_terminal_ensure_rejects_identity_collision() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis)
        terminal = _terminal()
        await replay.ensure_terminal(scope=SCOPE, event=terminal)
        conflicting = RunEventEnvelope(
            tenant_id=terminal.tenant_id,
            session_id=terminal.session_id,
            run_id=terminal.run_id,
            turn_id=terminal.turn_id,
            execution_attempt=terminal.execution_attempt,
            event_sequence=terminal.event_sequence,
            event=LiveTurnFailed(),
            occurred_at=terminal.occurred_at,
        )

        with pytest.raises(DeliveryUnavailableError):
            await replay.ensure_terminal(scope=SCOPE, event=conflicting)
        assert len(redis.streams["agentos:events:tenant_1:session_1:run_1"]) == 1

    asyncio.run(scenario())


def test_terminal_ensure_rejects_nonterminal_sentinel_event() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis)
        event = _event(9_007_199_254_740_991, "not terminal")

        with pytest.raises(ValueError, match="terminal or waiting"):
            await replay.ensure_terminal(scope=SCOPE, event=event)
        assert redis.streams == {}

    asyncio.run(scenario())


def test_terminal_ensure_reappends_after_replay_trim() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        replay = RedisEventReplayAdapter(client=redis, max_events=1)
        terminal = _terminal()
        first = await replay.ensure_terminal(scope=SCOPE, event=terminal)
        await replay.append(scope=SCOPE, event=_event(0, "later"))

        restored = await replay.ensure_terminal(scope=SCOPE, event=terminal)

        assert restored.cursor != first.cursor
        assert restored.event == terminal
        batch = await replay.replay(
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            after=None,
            limit=1,
        )
        assert batch == ReplayBatch((restored,), restored.cursor)

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
