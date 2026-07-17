import asyncio

import pytest

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, WaitRequest
from agentos.durable import DurableRuntimeProfile
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    AgentResult,
    AgentStream,
    AgentWaiting,
    DurableCommandReceipt,
    DurableRunCommand,
    WaitReason,
)
from agentos.runtime.errors import DurableCommandUnsupportedError
from agentos.runtime.run_state import RunStatus


def _wait_tool() -> RegisteredTool:
    async def wait_for_answer(_arguments: dict[str, object]) -> WaitRequest:
        return WaitRequest(WaitReason("human_input", "approval_1"))

    return RegisteredTool(
        "wait_for_answer",
        "Wait for a human answer.",
        {"type": "object"},
        wait_for_answer,
    )


def test_profile_restarts_and_continues_same_run_with_new_turn(tmp_path) -> None:
    async def run() -> None:
        first_provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "wait_for_answer", {}),
                    ),
                ),
            ],
        )
        paths = {
            "database_path": tmp_path / "state.db",
            "artifact_root": tmp_path / "artifacts",
        }

        with DurableRuntimeProfile(
            agent_builder=(
                AgentBuilder().provider(first_provider).tools([_wait_tool()])
            ),
            **paths,
        ) as first_profile:
            first_agent = first_profile.build_agent(session_id="session_1")
            waiting = await first_agent.run("review drawing")
            assert isinstance(waiting, AgentWaiting)
            run_id = waiting.run_id
            assert first_agent.query_loop.session_state.next_turn_number() == 2

        command = DurableRunCommand(
            run_id,
            "command_1",
            "hitl_answer",
            {"answer": "approved-secret-value"},
        )
        restarted_provider = FakeProvider(
            [ProviderResponse("resumed after restart")]
        )
        with DurableRuntimeProfile(
            agent_builder=(
                AgentBuilder().provider(restarted_provider).tools([_wait_tool()])
            ),
            **paths,
        ) as restarted_profile:
            restarted = restarted_profile.build_agent(session_id="session_1")
            result = await restarted.run(command)

            assert result == AgentResult("resumed after restart")
            assert restarted.query_loop.run_runtime.get_run(run_id).status is RunStatus.COMPLETED
            assert restarted.query_loop.session_state.next_turn_number() == 3
            assert all(
                "approved-secret-value" not in message.content
                for message in restarted.query_loop.message_runtime.store.all()
            )

            duplicate = await restarted.run(command)
            assert duplicate == DurableCommandReceipt(
                run_id,
                "command_1",
                "hitl_answer",
                aggregate_version=4,
                duplicate=True,
            )
            assert len(first_provider.requests) == 1
            assert len(restarted_provider.requests) == 1

        continuation = restarted_provider.requests[0].messages[-1]
        assert continuation.kind == "continuation_data"
        text = continuation.content[0].text  # type: ignore[union-attr]
        assert 'authority="context-data"' in text
        assert 'persistence="ephemeral"' in text
        assert 'visibility="internal"' in text
        assert 'kind="hitl_answer"' in text
        assert "approved-secret-value" in text

    asyncio.run(run())


def test_profile_recovers_command_accepted_before_loop_entry(tmp_path) -> None:
    async def run() -> None:
        provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "wait_for_answer", {}),
                    ),
                ),
                ProviderResponse("recovered pending continuation"),
            ],
        )
        builder = AgentBuilder().provider(provider).tools([_wait_tool()])
        profile_args = {
            "agent_builder": builder,
            "database_path": tmp_path / "state.db",
            "artifact_root": tmp_path / "artifacts",
        }

        with DurableRuntimeProfile(**profile_args) as profile:
            agent = profile.build_agent(session_id="session_1")
            waiting = await agent.run("start")
            assert isinstance(waiting, AgentWaiting)
            command = DurableRunCommand(
                waiting.run_id,
                "command_before_crash",
                "hitl_answer",
                {"answer": "continue"},
            )
            accepted = agent._durable_command_runtime.accept(command)
            assert accepted.run_id == waiting.run_id

        with DurableRuntimeProfile(**profile_args) as restarted_profile:
            restarted = restarted_profile.build_agent(session_id="session_1")
            result = await restarted.run(command)

        assert result == AgentResult("recovered pending continuation")
        assert len(provider.requests) == 2

    asyncio.run(run())


def test_local_agent_rejects_durable_command_without_command_runtime() -> None:
    async def run() -> None:
        provider = FakeProvider([])
        agent = AgentBuilder().provider(provider).build(session_id="session_1")

        with pytest.raises(
            DurableCommandUnsupportedError,
            match="durable command runtime is not configured",
        ):
            await agent.run(
                DurableRunCommand("run_1", "command_1", "resume", {})
            )
        assert provider.requests == []

    asyncio.run(run())


def test_closing_unconsumed_command_stream_does_not_reaccept_command(
    tmp_path,
) -> None:
    async def run() -> None:
        provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_1", "wait_for_answer", {}),
                    ),
                ),
            ],
        )
        builder = AgentBuilder().provider(provider).tools([_wait_tool()])
        profile_args = {
            "agent_builder": builder,
            "database_path": tmp_path / "state.db",
            "artifact_root": tmp_path / "artifacts",
        }

        with DurableRuntimeProfile(**profile_args) as profile:
            agent = profile.build_agent(session_id="session_1")
            waiting = await agent.run("start")
            assert isinstance(waiting, AgentWaiting)
            command = DurableRunCommand(
                waiting.run_id,
                "command_stream_1",
                "hitl_answer",
                {"answer": "continue"},
            )

            stream = await agent.run(command, stream=True)
            assert isinstance(stream, AgentStream)
            await stream.aclose()
            assert stream.closed
            assert agent.query_loop.run_runtime.get_run(waiting.run_id).status is RunStatus.CANCELLED

            duplicate = await agent.run(command)
            assert isinstance(duplicate, DurableCommandReceipt)
            assert duplicate.duplicate is True
            assert len(provider.requests) == 1

    asyncio.run(run())
