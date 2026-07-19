import asyncio

import pytest

from agentos.runtime import AgentResult, RunRequest, TurnStreamCompleted, UserTurnInput
from agentos.runtime.errors import AgentBusyError
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime
from agentos.runtime.run_state import RunNotFoundError
from tests.runtime._query_loop_contract_fixtures import (
    make_query_loop,
    make_recording_agent,
)


def test_agent_has_one_run_entry_and_both_modes_share_events() -> None:
    async def run() -> None:
        agent, recorder = make_recording_agent()

        outcome = await agent.run("hello")
        stream = await agent.run("hello", stream=True)
        async with stream:
            streamed = [event async for event in stream]

        assert outcome == AgentResult("answer")
        assert recorder.control_flow_runs == 2
        assert recorder.provider_calls == 2
        assert isinstance(streamed[-1], TurnStreamCompleted)
        assert not hasattr(agent, "async_run")
        assert not hasattr(agent, "async_stream")
        assert not hasattr(agent, "stream")

    asyncio.run(run())


def test_query_loop_rejects_concurrent_run_until_stream_cleanup() -> None:
    async def run() -> None:
        loop = make_query_loop()

        stream = await loop.execute(RunRequest(UserTurnInput("one")))
        with pytest.raises(AgentBusyError):
            await loop.execute(RunRequest(UserTurnInput("two")))
        await stream.aclose()

        replacement = await loop.execute(RunRequest(UserTurnInput("three")))
        await replacement.aclose()

    asyncio.run(run())


def test_query_loop_rejects_busy_execution_before_creating_run() -> None:
    async def run() -> None:
        run_ids = iter(("run_first", "run_busy"))
        runs = RunRuntime(
            session_id="session_1",
            store=InMemoryRunStore(),
            id_factory=lambda: next(run_ids),
        )
        loop = make_query_loop(run_runtime=runs)

        first = await loop.execute(RunRequest(UserTurnInput("one")))
        with pytest.raises(AgentBusyError):
            await loop.execute(RunRequest(UserTurnInput("two")))

        with pytest.raises(RunNotFoundError, match="run_busy"):
            await runs.get_run("run_busy")
        await first.aclose()

    asyncio.run(run())
