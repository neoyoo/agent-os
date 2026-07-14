import asyncio
from threading import Event as ThreadEvent, Thread

import pytest

from agentos.runtime import AgentResult
from agentos.runtime.errors import (
    SyncAdapterEventLoopError,
    SyncAgentClosedError,
)
from agentos.sync import SyncAdapterReentryError, SyncAgent
import agentos.sync.agent as sync_agent_module
from tests.runtime._query_loop_contract_fixtures import make_recording_agent
from tests.sync._sync_agent_close_fixtures import (
    OrderedStreamRegistry as _OrderedStreamRegistry,
    RegisteredStreamProbe as _RegisteredStreamProbe,
    close_agent_thread as _close_agent_thread,
    observe_condition_wait as _observe_condition_wait,
    register_blocking_stream as _register_blocking_stream,
)


def test_sync_agent_uses_same_agent_and_closes_stream() -> None:
    agent, recorder = make_recording_agent()

    with SyncAgent(agent) as sync_agent:
        assert sync_agent.run("hello") == AgentResult("answer")
        with sync_agent.run("hello", stream=True) as stream:
            events = list(stream)

    assert events[-1].content == "answer"
    assert recorder.provider_calls == 2


def test_sync_agent_rejects_running_loop_and_closed_calls() -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)

    async def call_from_loop() -> None:
        with pytest.raises(SyncAdapterEventLoopError):
            sync_agent.run("hello")

    try:
        asyncio.run(call_from_loop())
    finally:
        sync_agent.close()

    with pytest.raises(SyncAgentClosedError):
        sync_agent.run("hello")


def test_sync_agent_owner_thread_close_reentry_has_no_lifecycle_side_effects() -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    owner_thread = sync_agent._host._thread
    attempts: list[str] = []
    stream = _RegisteredStreamProbe("stream", attempts)
    sync_agent._streams.add(stream)  # type: ignore[arg-type]

    async def close_from_owner() -> None:
        sync_agent.close()

    try:
        with pytest.raises(SyncAdapterReentryError):
            sync_agent._submit(close_from_owner()).result(timeout=2)

        assert not sync_agent.closed
        with sync_agent._lock:
            assert sync_agent._state == "open"
            assert sync_agent._closing_thread_id is None
        assert attempts == []
        assert stream.calls == 0
        assert owner_thread.is_alive()

        sync_agent.close()
    finally:
        if owner_thread.is_alive():
            sync_agent._host.close()

    assert sync_agent.closed
    assert attempts == ["stream"]
    assert stream.calls == 1
    assert not owner_thread.is_alive()


def test_sync_agent_close_linearizes_with_stream_open(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    opening_started = ThreadEvent()
    release_opening = ThreadEvent()
    close_linearized = ThreadEvent()
    captured_streams = []
    returned_streams = []
    run_errors: list[BaseException] = []
    close_errors: list[BaseException] = []
    original_create_stream_driver = sync_agent_module.create_stream_driver
    original_condition_wait = sync_agent._stream_condition.wait

    async def blocked_create_stream_driver(stream):
        captured_streams.append(stream)
        opening_started.set()
        assert await asyncio.to_thread(release_opening.wait, 2)
        return await original_create_stream_driver(stream)

    def observed_condition_wait(timeout: float | None = None) -> bool:
        close_linearized.set()
        return original_condition_wait(timeout)

    monkeypatch.setattr(
        sync_agent_module,
        "create_stream_driver",
        blocked_create_stream_driver,
    )
    monkeypatch.setattr(
        sync_agent._stream_condition,
        "wait",
        observed_condition_wait,
    )

    def open_stream() -> None:
        try:
            returned_streams.append(sync_agent.run("hello", stream=True))
        except BaseException as error:
            run_errors.append(error)

    def close_agent() -> None:
        try:
            sync_agent.close()
        except BaseException as error:
            close_errors.append(error)

    run_thread = Thread(target=open_stream)
    close_thread = Thread(target=close_agent)
    try:
        run_thread.start()
        assert opening_started.wait(2)
        close_thread.start()
        assert close_linearized.wait(2)
        release_opening.set()
        run_thread.join(timeout=2)
        close_thread.join(timeout=2)

        assert not run_thread.is_alive()
        assert not close_thread.is_alive()
        assert close_errors == []
        assert len(captured_streams) == 1
        assert len(returned_streams) + len(run_errors) == 1
        if returned_streams:
            assert returned_streams[0].closed
        else:
            assert isinstance(run_errors[0], SyncAgentClosedError)
        assert captured_streams[0].closed
        assert asyncio.run(agent.run("replacement")) == AgentResult("answer")
    finally:
        release_opening.set()
        run_thread.join(timeout=2)
        close_thread.join(timeout=2)
        for stream in captured_streams:
            if not stream.closed:
                asyncio.run(stream.aclose())
        sync_agent.close()


def test_sync_agent_close_best_effort_stops_host_after_stream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    owner_thread = sync_agent._host._thread
    attempts: list[str] = []
    cleanup_error = RuntimeError("cleanup failed")
    failing = _RegisteredStreamProbe(
        "failing",
        attempts,
        error=cleanup_error,
    )
    succeeding = _RegisteredStreamProbe("succeeding", attempts)
    sync_agent._streams = _OrderedStreamRegistry(  # type: ignore[assignment]
        failing,
        succeeding,
    )
    original_host_close = sync_agent._host.close
    host_close_calls = 0

    def observed_host_close() -> None:
        nonlocal host_close_calls
        host_close_calls += 1
        original_host_close()

    monkeypatch.setattr(sync_agent._host, "close", observed_host_close)

    try:
        with pytest.raises(RuntimeError) as caught:
            sync_agent.close()
    finally:
        if owner_thread.is_alive():
            original_host_close()

    assert caught.value is cleanup_error
    assert set(attempts) == {"failing", "succeeding"}
    assert failing.calls == 1
    assert succeeding.calls == 1
    assert host_close_calls == 1
    assert sync_agent.closed
    assert not owner_thread.is_alive()


def test_concurrent_sync_agent_close_waits_for_closed_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    owner_thread = sync_agent._host._thread
    attempts: list[str] = []
    blocking, cleanup_entered, release_cleanup = _register_blocking_stream(
        sync_agent,
        attempts,
    )
    second_waiting = ThreadEvent()
    first_returned = ThreadEvent()
    second_returned = ThreadEvent()
    close_errors: list[BaseException] = []
    _observe_condition_wait(monkeypatch, sync_agent, second_waiting)

    first = _close_agent_thread(
        sync_agent,
        close_errors,
        returned=first_returned,
    )
    second = _close_agent_thread(
        sync_agent,
        close_errors,
        returned=second_returned,
    )
    try:
        first.start()
        assert cleanup_entered.wait(2)
        second.start()
        assert second_waiting.wait(2)
        assert not first_returned.is_set()
        assert not second_returned.is_set()

        release_cleanup.set()
        first.join(timeout=2)
        second.join(timeout=2)
    finally:
        release_cleanup.set()
        first.join(timeout=2)
        second.join(timeout=2)
        if owner_thread.is_alive():
            sync_agent._host.close()

    assert not first.is_alive()
    assert not second.is_alive()
    assert close_errors == []
    assert attempts == ["blocking"]
    assert blocking.calls == 1
    assert sync_agent.closed
    assert not owner_thread.is_alive()


def test_waiting_sync_agent_closer_is_notified_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    owner_thread = sync_agent._host._thread
    attempts: list[str] = []
    cleanup_error = RuntimeError("cleanup failed")
    blocking, cleanup_entered, release_cleanup = _register_blocking_stream(
        sync_agent,
        attempts,
        error=cleanup_error,
    )
    second_waiting = ThreadEvent()
    first_returned = ThreadEvent()
    second_returned = ThreadEvent()
    first_errors: list[BaseException] = []
    second_errors: list[BaseException] = []
    _observe_condition_wait(monkeypatch, sync_agent, second_waiting)

    first = _close_agent_thread(
        sync_agent,
        first_errors,
        returned=first_returned,
    )
    second = _close_agent_thread(
        sync_agent,
        second_errors,
        returned=second_returned,
    )
    try:
        first.start()
        assert cleanup_entered.wait(2)
        second.start()
        assert second_waiting.wait(2)
        assert not first_returned.is_set()
        assert not second_returned.is_set()

        release_cleanup.set()
        first.join(timeout=2)
        second.join(timeout=2)
    finally:
        release_cleanup.set()
        first.join(timeout=2)
        second.join(timeout=2)
        if owner_thread.is_alive():
            sync_agent._host.close()

    assert not first.is_alive()
    assert not second.is_alive()
    assert first_errors == [cleanup_error]
    assert second_errors == []
    assert attempts == ["blocking"]
    assert blocking.calls == 1
    assert sync_agent.closed
    assert not owner_thread.is_alive()


def test_sync_agent_closing_rejects_run_before_closed_publication() -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    owner_thread = sync_agent._host._thread
    attempts: list[str] = []
    _, cleanup_entered, release_cleanup = _register_blocking_stream(
        sync_agent,
        attempts,
    )
    close_errors: list[BaseException] = []
    closer = _close_agent_thread(sync_agent, close_errors)

    try:
        closer.start()
        assert cleanup_entered.wait(2)
        assert not sync_agent.closed
        with pytest.raises(SyncAgentClosedError):
            sync_agent.run("late")
        assert owner_thread.is_alive()

        release_cleanup.set()
        closer.join(timeout=2)
    finally:
        release_cleanup.set()
        closer.join(timeout=2)
        if owner_thread.is_alive():
            sync_agent._host.close()

    assert not closer.is_alive()
    assert close_errors == []
    assert sync_agent.closed
    assert not owner_thread.is_alive()


def test_sync_agent_stream_reservation_rejects_closing_and_closed_states() -> None:
    agent, recorder = make_recording_agent()
    sync_agent = SyncAgent(agent)
    owner_thread = sync_agent._host._thread
    attempts: list[str] = []
    _, cleanup_entered, release_cleanup = _register_blocking_stream(
        sync_agent,
        attempts,
    )
    close_errors: list[BaseException] = []
    closer = _close_agent_thread(sync_agent, close_errors)

    try:
        closer.start()
        assert cleanup_entered.wait(2)
        assert not sync_agent.closed
        with pytest.raises(SyncAgentClosedError):
            sync_agent.run("closing", stream=True)
        assert owner_thread.is_alive()

        release_cleanup.set()
        closer.join(timeout=2)

        with pytest.raises(SyncAgentClosedError):
            sync_agent.run("closed", stream=True)
    finally:
        release_cleanup.set()
        closer.join(timeout=2)
        if owner_thread.is_alive():
            sync_agent._host.close()

    assert not closer.is_alive()
    assert close_errors == []
    assert attempts == ["blocking"]
    assert recorder.provider_calls == 0
    assert sync_agent.closed
    assert not owner_thread.is_alive()


@pytest.mark.parametrize("stream_fails", [True, False])
def test_sync_agent_close_error_priority_and_terminal_idempotence(
    monkeypatch: pytest.MonkeyPatch,
    stream_fails: bool,
) -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    owner_thread = sync_agent._host._thread
    attempts: list[str] = []
    stream_error = RuntimeError("cleanup failed")
    host_error = RuntimeError("host cleanup failed")
    stream = _RegisteredStreamProbe(
        "stream",
        attempts,
        error=stream_error if stream_fails else None,
    )
    sync_agent._streams.add(stream)  # type: ignore[arg-type]
    original_host_close = sync_agent._host.close
    host_close_calls = 0

    def failing_host_close() -> None:
        nonlocal host_close_calls
        host_close_calls += 1
        original_host_close()
        raise host_error

    monkeypatch.setattr(sync_agent._host, "close", failing_host_close)

    try:
        with pytest.raises(RuntimeError) as caught:
            sync_agent.close()

        assert caught.value is (stream_error if stream_fails else host_error)
        assert attempts == ["stream"]
        assert host_close_calls == 1
        assert sync_agent.closed
        assert not owner_thread.is_alive()

        sync_agent.close()
        assert host_close_calls == 1
    finally:
        if owner_thread.is_alive():
            original_host_close()


def test_sync_agent_close_allows_cleanup_owner_reentry() -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    attempts: list[str] = []
    reentrant = _RegisteredStreamProbe(
        "reentrant",
        attempts,
        reenter=sync_agent.close,
    )
    sync_agent._streams.add(reentrant)  # type: ignore[arg-type]
    close_errors: list[BaseException] = []

    def close_agent() -> None:
        try:
            sync_agent.close()
        except BaseException as error:
            close_errors.append(error)

    closer = Thread(target=close_agent, daemon=True)
    closer.start()
    closer.join(timeout=2)

    assert not closer.is_alive()
    assert close_errors == []
    assert attempts == ["reentrant"]
    assert reentrant.calls == 1
    assert sync_agent.closed
