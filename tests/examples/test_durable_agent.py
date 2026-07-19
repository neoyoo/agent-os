import asyncio

from agentos.examples.durable_agent import build_durable_profile
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    AgentResult,
    AgentWaiting,
    DurableRunCommand,
)


def test_durable_example_restarts_and_resumes_same_run(tmp_path) -> None:
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        "call_1",
                        "wait_for_approval",
                        {},
                    ),
                )
            ),
            "approved",
        ]
    )

    async def scenario() -> tuple[AgentWaiting, AgentResult]:
        with build_durable_profile(provider, tmp_path) as first:
            first_agent = await first.build_agent("session_1")
            waiting = await first_agent.run("submit")
            assert isinstance(waiting, AgentWaiting)

        with build_durable_profile(provider, tmp_path) as restarted:
            restarted_agent = await restarted.build_agent("session_1")
            result = await restarted_agent.run(
                DurableRunCommand(
                    waiting.run_id,
                    "approval_command_1",
                    "hitl_answer",
                    {"answer": "approved"},
                )
            )
            assert isinstance(result, AgentResult)
        return waiting, result

    waiting, result = asyncio.run(scenario())

    assert result == AgentResult("approved")
    assert len(provider.requests) == 2
