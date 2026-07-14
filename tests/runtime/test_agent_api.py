import asyncio

import pytest

from agentos.runtime import (
    LocalContinuationInput,
    StatusUpdate,
    TurnStreamFailed,
    iter_sse,
)
from agentos.runtime.errors import ContinuationUnavailableError, RunProtocolError
from tests.runtime._query_loop_contract_fixtures import (
    agent_stream_from,
    make_recording_agent,
)


def test_local_continuation_requires_pending_notice() -> None:
    async def run() -> None:
        agent, _ = make_recording_agent()
        with pytest.raises(ContinuationUnavailableError):
            await agent.run(LocalContinuationInput())

    asyncio.run(run())


def test_collector_rejects_stream_without_outcome() -> None:
    async def run() -> None:
        agent, _ = make_recording_agent()
        stream = agent_stream_from(StatusUpdate("stage", "working"))
        with pytest.raises(RunProtocolError, match="without an outcome"):
            await agent._collect_outcome(stream)

    asyncio.run(run())


def test_failure_event_is_followed_by_same_exception_object() -> None:
    async def run() -> None:
        error = RuntimeError("provider failed")
        stream = agent_stream_from(TurnStreamFailed(error), final_error=error)

        assert await anext(stream) == TurnStreamFailed(error)
        with pytest.raises(RuntimeError) as caught:
            await anext(stream)
        assert caught.value is error

    asyncio.run(run())


def test_iter_sse_only_projects_existing_stream() -> None:
    async def run() -> None:
        agent, recorder = make_recording_agent()
        stream = await agent.run("hello", stream=True)
        async with stream:
            chunks = [chunk async for chunk in iter_sse(stream)]

        assert chunks[-1].startswith("event: done")
        assert recorder.provider_calls == 1

    asyncio.run(run())


def test_interrupt_created_stream_prevents_turn_start() -> None:
    async def run() -> None:
        agent, recorder = make_recording_agent()
        stream = await agent.run("hello", stream=True)

        assert agent.interrupt() is True
        assert [event async for event in stream] == []
        assert recorder.control_flow_runs == 0
        assert agent.interrupt() is False

    asyncio.run(run())
