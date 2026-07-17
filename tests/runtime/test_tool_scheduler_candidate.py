import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager, suppress
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from agentos.capabilities import WaitRequest
from agentos.capabilities.executor import ToolExecutionOutcome, ToolExecutionResult
from agentos.providers import ProviderToolCall
from agentos.runtime import WaitReason
from agentos.runtime.tool_scheduler import (
    ScheduledToolCallResult,
    ToolCallScheduler,
    ToolConcurrencyPolicy,
    ToolExecutionContext,
)


_TIMER_DUE = datetime(2026, 7, 17, 12, tzinfo=UTC)


def _parallel(call_id: str) -> ProviderToolCall:
    return ProviderToolCall(
        id=call_id,
        name=f"parallel_{call_id}",
        arguments={},
    )


def _exclusive(call_id: str) -> ProviderToolCall:
    return ProviderToolCall(
        id=call_id,
        name=f"exclusive_{call_id}",
        arguments={},
    )


async def _checkpoint() -> None:
    checkpoint = asyncio.Event()
    asyncio.get_running_loop().call_soon(checkpoint.set)
    await checkpoint.wait()


async def _cancel_and_await(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


class ConcurrencyProbe:
    def __init__(
        self,
        *,
        blocked: tuple[str, ...] = (),
        failing: tuple[str, ...] = (),
    ) -> None:
        self.blocked = set(blocked)
        self.failing = set(failing)
        self.started = {call_id: asyncio.Event() for call_id in blocked + failing}
        self.release = {call_id: asyncio.Event() for call_id in blocked + failing}
        self.start_order: list[str] = []
        self.active: set[str] = set()
        self.active_when_started: dict[str, set[str]] = {}
        self.overlaps: dict[str, set[str]] = {}
        self.cancellations: set[str] = set()
        self.max_concurrency = 0

    def policy_for(self, call: ProviderToolCall) -> ToolConcurrencyPolicy:
        if call.name.startswith("parallel_"):
            return ToolConcurrencyPolicy.PARALLEL_SAFE
        return ToolConcurrencyPolicy.EXCLUSIVE

    async def execute(
        self,
        call: ProviderToolCall,
        _context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        call_id = call.id
        already_active = set(self.active)
        self.start_order.append(call_id)
        self.active_when_started[call_id] = already_active
        self.overlaps[call_id] = set(already_active)
        for active_id in already_active:
            self.overlaps[active_id].add(call_id)
        self.active.add(call_id)
        self.max_concurrency = max(self.max_concurrency, len(self.active))
        self.started.setdefault(call_id, asyncio.Event()).set()
        try:
            if call_id in self.blocked or call_id in self.failing:
                await self.release[call_id].wait()
            if call_id in self.failing:
                raise RuntimeError(f"failed: {call_id}")
            return ToolExecutionResult(tool_call_id=call_id, content=call_id)
        except asyncio.CancelledError:
            self.cancellations.add(call_id)
            raise
        finally:
            self.active.remove(call_id)

    def release_all(self) -> None:
        for barrier in self.release.values():
            barrier.set()


@asynccontextmanager
async def _probe_task(
    probe: ConcurrencyProbe,
    *calls: ProviderToolCall,
    max_parallel_calls: int = 2,
    policy_for: Callable[[ProviderToolCall], ToolConcurrencyPolicy] | None = None,
) -> AsyncIterator[asyncio.Task[tuple[ScheduledToolCallResult, ...]]]:
    task = asyncio.create_task(
        ToolCallScheduler(max_parallel_calls=max_parallel_calls).execute_batch(
            calls=calls,
            policy_for=policy_for or probe.policy_for,
            execute=probe.execute,
        ),
    )
    try:
        yield task
    finally:
        probe.release_all()
        if not task.done():
            await _cancel_and_await(task)
        elif not task.cancelled() and (error := task.exception()) is not None:
            raise error


@pytest.mark.parametrize(
    "invalid",
    [True, False, 0, -1, 1.5, float("inf"), float("nan"), "2", None],
)
def test_candidate_rejects_invalid_parallel_limit(invalid: object) -> None:
    with pytest.raises(
        ValueError,
        match="max_parallel_calls must be an integer greater than zero",
    ):
        ToolCallScheduler(max_parallel_calls=invalid)


def test_candidate_defaults_parallel_limit_to_eight() -> None:
    assert ToolCallScheduler().max_parallel_calls == 8


def test_scheduler_reports_structured_metadata_for_queue_and_barrier() -> None:
    async def run() -> None:
        slow_started = asyncio.Event()
        queued_started = asyncio.Event()
        exclusive_started = asyncio.Event()
        release_slow = asyncio.Event()
        release_exclusive = asyncio.Event()
        contexts: dict[str, ToolExecutionContext] = {}
        completions: list[ScheduledToolCallResult] = []

        async def execute(
            call: ProviderToolCall,
            context: ToolExecutionContext,
        ) -> ToolExecutionResult:
            contexts[call.id] = context
            if call.id == "slow":
                slow_started.set()
                await release_slow.wait()
            elif call.id == "queued":
                queued_started.set()
            elif call.id == "exclusive":
                exclusive_started.set()
                await release_exclusive.wait()
            return ToolExecutionResult(call.id, call.id)

        scheduler = ToolCallScheduler(max_parallel_calls=2)
        task = asyncio.create_task(
            scheduler.execute_batch(
                calls=(
                    _parallel("slow"),
                    _parallel("fast"),
                    _parallel("queued"),
                    _exclusive("exclusive"),
                ),
                batch_indexes=(1, 2, 4, 5),
                batch_size=6,
                policy_for=lambda call: (
                    ToolConcurrencyPolicy.EXCLUSIVE
                    if call.id == "exclusive"
                    else ToolConcurrencyPolicy.PARALLEL_SAFE
                ),
                execute=execute,
                on_completed=completions.append,
            ),
        )
        try:
            async with asyncio.timeout(2):
                await slow_started.wait()
                await queued_started.wait()
                assert not exclusive_started.is_set()

                release_slow.set()
                await exclusive_started.wait()
                release_exclusive.set()
                results = await task
        finally:
            release_slow.set()
            release_exclusive.set()
            if not task.done():
                await _cancel_and_await(task)

        assert [item.index for item in results] == [1, 2, 4, 5]
        assert [item.index for item in completions] == [2, 4, 1, 5]
        assert contexts["slow"].batch_index == 1
        assert contexts["queued"].batch_index == 4
        assert contexts["exclusive"].batch_index == 5
        assert contexts["slow"].concurrency_policy is ToolConcurrencyPolicy.PARALLEL_SAFE
        assert contexts["exclusive"].concurrency_policy is ToolConcurrencyPolicy.EXCLUSIVE
        assert all(context.max_parallel_calls == 2 for context in contexts.values())
        assert all(context.batch_size == 6 for context in contexts.values())
        assert all(context.queue_wait_seconds >= 0 for context in contexts.values())
        assert contexts["queued"].queue_wait_seconds >= contexts["fast"].queue_wait_seconds
        assert contexts["exclusive"].queue_wait_seconds >= contexts["queued"].queue_wait_seconds
        assert all(
            item.execution_duration_seconds is not None
            and item.execution_duration_seconds >= 0
            for item in completions
        )

    asyncio.run(run())


def test_scheduled_result_is_frozen_and_has_no_instance_dict() -> None:
    call = _parallel("immutable")
    result = ScheduledToolCallResult(
        index=0,
        tool_call=call,
        result=ToolExecutionResult(tool_call_id=call.id, content="immutable"),
    )

    with pytest.raises(FrozenInstanceError):
        result.index = 1  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


def test_candidate_defaults_unknown_policy_to_exclusive() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe(blocked=("first",))
        async with _probe_task(
            probe,
            _parallel("first"),
            _parallel("second"),
            max_parallel_calls=8,
            policy_for=lambda _call: None,  # type: ignore[arg-type,return-value]
        ) as task:
            async with asyncio.timeout(2):
                await probe.started["first"].wait()
                await _checkpoint()

                assert "second" not in probe.active_when_started

                probe.release["first"].set()
                await task
                assert probe.overlaps == {"first": set(), "second": set()}

    asyncio.run(run())


def test_candidate_preserves_order_and_fifo_limit() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe(blocked=("slow",))
        async with _probe_task(
            probe,
            _parallel("slow"),
            _parallel("fast"),
            _parallel("queued"),
        ) as task:
            async with asyncio.timeout(2):
                await probe.started.setdefault("queued", asyncio.Event()).wait()

                assert probe.start_order == ["slow", "fast", "queued"]
                assert probe.max_concurrency == 2
                assert probe.active_when_started["queued"] == {"slow"}

                probe.release["slow"].set()
                results = await task
                assert [item.tool_call.id for item in results] == [
                    "slow",
                    "fast",
                    "queued",
                ]

    asyncio.run(run())


def test_candidate_applies_exclusive_barriers() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe(blocked=("a", "b", "c"))
        async with _probe_task(
            probe,
            _parallel("a"),
            _exclusive("b"),
            _parallel("c"),
        ) as task:
            async with asyncio.timeout(2):
                await probe.started["a"].wait()
                await _checkpoint()
                assert "b" not in probe.active_when_started
                assert "c" not in probe.active_when_started

                probe.release["a"].set()
                await probe.started["b"].wait()
                await _checkpoint()
                assert probe.active_when_started["b"] == set()
                assert "c" not in probe.active_when_started

                probe.release["b"].set()
                await probe.started["c"].wait()
                assert probe.active_when_started["c"] == set()
                probe.release["c"].set()
                await task

                assert probe.start_order == ["a", "b", "c"]
                assert probe.overlaps == {"a": set(), "b": set(), "c": set()}

    asyncio.run(run())


def test_candidate_cancel_wins_when_exclusive_swallows_cancellation() -> None:
    async def run() -> None:
        first_started = asyncio.Event()
        cancellation_swallowed = asyncio.Event()
        release_first = asyncio.Event()
        started: list[str] = []

        async def execute(
            call: ProviderToolCall,
            _context: ToolExecutionContext,
        ) -> ToolExecutionResult:
            started.append(call.id)
            if call.id == "first":
                first_started.set()
                try:
                    await release_first.wait()
                except asyncio.CancelledError:
                    cancellation_swallowed.set()
                    await release_first.wait()
            return ToolExecutionResult(call.id, call.id)

        task = asyncio.create_task(
            ToolCallScheduler().execute_batch(
                calls=(_exclusive("first"), _exclusive("second")),
                policy_for=lambda _call: ToolConcurrencyPolicy.EXCLUSIVE,
                execute=execute,
            ),
        )
        try:
            async with asyncio.timeout(2):
                await first_started.wait()
                assert task.cancel()
                await cancellation_swallowed.wait()
                assert task.cancelling() == 1

                release_first.set()
                observed_task = task
                task = None
                with pytest.raises(asyncio.CancelledError):
                    await observed_task

                assert observed_task.cancelled()
                assert observed_task.cancelling() == 1
                assert started == ["first"]
        finally:
            release_first.set()
            if task is not None:
                await _cancel_and_await(task)

    asyncio.run(run())


def test_candidate_failure_cancels_pending_and_never_starts_queued_work() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe(blocked=("running",), failing=("failure",))
        with pytest.raises(RuntimeError, match="failed: failure"):
            async with _probe_task(
                probe,
                _parallel("success"),
                _parallel("running"),
                _parallel("failure"),
                _parallel("queued"),
            ) as task:
                async with asyncio.timeout(2):
                    await probe.started["running"].wait()
                    await probe.started["failure"].wait()
                    assert probe.start_order == ["success", "running", "failure"]
                    probe.release["failure"].set()
                    await task

        assert probe.cancellations == {"running"}
        assert "queued" not in probe.active_when_started
        assert probe.active == set()

    asyncio.run(run())


def test_candidate_repeated_cancel_during_failure_cleanup_wins() -> None:
    async def run() -> None:
        failure_started = asyncio.Event()
        failure_release = asyncio.Event()
        sibling_started = asyncio.Event()
        sibling_release = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        started: list[str] = []
        active: set[str] = set()

        async def execute(
            call: ProviderToolCall,
            _context: ToolExecutionContext,
        ) -> ToolExecutionResult:
            started.append(call.id)
            active.add(call.id)
            try:
                if call.id == "failure":
                    failure_started.set()
                    await failure_release.wait()
                    raise RuntimeError("failed: failure")
                if call.id == "sibling":
                    sibling_started.set()
                    try:
                        await sibling_release.wait()
                    except asyncio.CancelledError:
                        cleanup_started.set()
                        await cleanup_release.wait()
                        raise
                raise AssertionError(f"unexpectedly started: {call.id}")
            finally:
                active.remove(call.id)

        scheduler = ToolCallScheduler(max_parallel_calls=2)
        task: asyncio.Task[tuple[ScheduledToolCallResult, ...]] | None = (
            asyncio.create_task(
                scheduler.execute_batch(
                    calls=(
                        _parallel("failure"),
                        _parallel("sibling"),
                        _parallel("queued"),
                    ),
                    policy_for=lambda _call: ToolConcurrencyPolicy.PARALLEL_SAFE,
                    execute=execute,
                ),
            )
        )
        try:
            async with asyncio.timeout(2):
                await failure_started.wait()
                await sibling_started.wait()
                failure_release.set()
                await cleanup_started.wait()

                assert task.cancel()
                assert task.cancel()
                assert task.cancelling() == 2
                await _checkpoint()

                assert not task.done()

                cleanup_release.set()
                observed_task = task
                task = None
                with pytest.raises(asyncio.CancelledError):
                    await observed_task

                assert observed_task.cancelled()
                assert active == set()
                assert "queued" not in started
        finally:
            failure_release.set()
            sibling_release.set()
            cleanup_release.set()
            if task is not None:
                await _cancel_and_await(task)

    asyncio.run(run())


def test_candidate_selects_first_error_by_provider_index() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe(failing=("first", "second"))
        with pytest.raises(RuntimeError, match="failed: first"):
            async with _probe_task(
                probe,
                _parallel("first"),
                _parallel("second"),
            ) as task:
                async with asyncio.timeout(2):
                    await probe.started["first"].wait()
                    await probe.started["second"].wait()
                    probe.release["second"].set()
                    probe.release["first"].set()
                    await task

    asyncio.run(run())


def test_candidate_external_cancel_converges_submitted_work() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe(blocked=("one", "two"))
        async with _probe_task(
            probe,
            _parallel("one"),
            _parallel("two"),
            _parallel("queued"),
        ) as task:
            async with asyncio.timeout(2):
                await probe.started["one"].wait()
                await probe.started["two"].wait()
                assert task.cancel()
                assert task.cancel()
                assert task.cancelling() == 2

                with pytest.raises(asyncio.CancelledError):
                    await task

                assert task.cancelled()
                assert probe.cancellations == {"one", "two"}
                assert "queued" not in probe.active_when_started
                assert probe.active == set()

    asyncio.run(run())


def test_candidate_returns_results_by_provider_index_not_completion_order() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe(blocked=("first", "second"))
        async with _probe_task(
            probe,
            _parallel("first"),
            _parallel("second"),
        ) as task:
            async with asyncio.timeout(2):
                await probe.started["first"].wait()
                await probe.started["second"].wait()
                probe.release["second"].set()
                await _checkpoint()
                probe.release["first"].set()

                results = await task
                assert [item.tool_call.id for item in results] == ["first", "second"]

    asyncio.run(run())


def test_candidate_waits_for_sync_future_cleanup_before_failure_resolves() -> None:
    async def run() -> None:
        sync_future: Future[ToolExecutionResult] = Future()
        assert sync_future.set_running_or_notify_cancel()
        cleanup_started = asyncio.Event()
        failure_release = asyncio.Event()

        async def execute(
            call: ProviderToolCall,
            _context: ToolExecutionContext,
        ) -> ToolExecutionResult:
            if call.id == "failure":
                await failure_release.wait()
                raise RuntimeError("failed: failure")
            try:
                return await asyncio.wrap_future(sync_future)
            except asyncio.CancelledError:
                cleanup_started.set()
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(asyncio.wrap_future(sync_future))
                raise

        scheduler = ToolCallScheduler(max_parallel_calls=2)
        task = asyncio.create_task(
            scheduler.execute_batch(
                calls=(_parallel("sync"), _parallel("failure")),
                policy_for=lambda _call: ToolConcurrencyPolicy.PARALLEL_SAFE,
                execute=execute,
            ),
        )
        pending_task = task
        try:
            async with asyncio.timeout(2):
                failure_release.set()
                await cleanup_started.wait()
                await _checkpoint()

                assert not task.done()

                sync_future.set_result(ToolExecutionResult("sync", "sync"))
                with pytest.raises(RuntimeError, match="failed: failure"):
                    await task
                pending_task = None
        finally:
            failure_release.set()
            if not sync_future.done():
                sync_future.set_result(ToolExecutionResult("sync", "sync"))
            if pending_task is not None:
                await _cancel_and_await(pending_task)

    asyncio.run(run())


def test_candidate_exclusive_wait_stops_later_calls() -> None:
    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        started: list[str] = []

        async def execute(
            call: ProviderToolCall,
            _context: ToolExecutionContext,
        ) -> ToolExecutionOutcome:
            started.append(call.id)
            if call.id == "wait":
                return WaitRequest(reason)
            return ToolExecutionResult(call.id, "side effect")

        results = await ToolCallScheduler().execute_batch(
            calls=(_exclusive("wait"), _exclusive("side_effect")),
            policy_for=lambda _call: ToolConcurrencyPolicy.EXCLUSIVE,
            execute=execute,
        )

        assert started == ["wait"]
        assert [item.tool_call.id for item in results] == ["wait"]
        assert results[0].result == WaitRequest(reason)

    asyncio.run(run())


def test_candidate_parallel_wait_stops_refill_and_drains_running_calls() -> None:
    async def run() -> None:
        reason = WaitReason("timer", "timer_1", not_before=_TIMER_DUE)
        running_started = asyncio.Event()
        release_running = asyncio.Event()
        started: list[str] = []

        async def execute(
            call: ProviderToolCall,
            _context: ToolExecutionContext,
        ) -> ToolExecutionOutcome:
            started.append(call.id)
            if call.id == "wait":
                return WaitRequest(reason)
            if call.id == "running":
                running_started.set()
                await release_running.wait()
            return ToolExecutionResult(call.id, call.id)

        task = asyncio.create_task(
            ToolCallScheduler(max_parallel_calls=2).execute_batch(
                calls=(
                    _parallel("wait"),
                    _parallel("running"),
                    _parallel("queued"),
                    _exclusive("later_segment"),
                ),
                policy_for=lambda call: (
                    ToolConcurrencyPolicy.EXCLUSIVE
                    if call.id == "later_segment"
                    else ToolConcurrencyPolicy.PARALLEL_SAFE
                ),
                execute=execute,
            ),
        )
        try:
            async with asyncio.timeout(2):
                await running_started.wait()
                await _checkpoint()
                assert started == ["wait", "running"]
                release_running.set()
                results = await task
        finally:
            release_running.set()
            if not task.done():
                await _cancel_and_await(task)

        assert [item.tool_call.id for item in results] == ["wait", "running"]
        assert results[0].result == WaitRequest(reason)

    asyncio.run(run())


def test_candidate_parallel_wait_external_cancel_preserves_reason() -> None:
    async def run() -> None:
        reason = WaitReason("timer", "timer_1", not_before=_TIMER_DUE)
        running_started = asyncio.Event()
        running_cancelled = asyncio.Event()
        started: list[str] = []

        async def execute(
            call: ProviderToolCall,
            _context: ToolExecutionContext,
        ) -> ToolExecutionOutcome:
            started.append(call.id)
            if call.id == "wait":
                return WaitRequest(reason)
            if call.id == "running":
                running_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    running_cancelled.set()
                    raise
            return ToolExecutionResult(call.id, call.id)

        consumer = asyncio.create_task(
            ToolCallScheduler(max_parallel_calls=2).execute_batch(
                calls=(
                    _parallel("wait"),
                    _parallel("running"),
                    _parallel("queued"),
                ),
                policy_for=lambda _call: ToolConcurrencyPolicy.PARALLEL_SAFE,
                execute=execute,
            ),
        )
        async with asyncio.timeout(2):
            await running_started.wait()
            await _checkpoint()
            assert started == ["wait", "running"]

            consumer.cancel("consumer-stop")
            with pytest.raises(asyncio.CancelledError) as caught:
                await consumer

        assert caught.value.args == ("consumer-stop",)
        assert consumer.cancelled()
        assert running_cancelled.is_set()
        assert "queued" not in started

    asyncio.run(run())
