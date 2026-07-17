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

    with build_durable_profile(provider, tmp_path) as first:
        waiting = asyncio.run(first.build_agent("session_1").run("submit"))
        assert isinstance(waiting, AgentWaiting)

    with build_durable_profile(provider, tmp_path) as restarted:
        result = asyncio.run(
            restarted.build_agent("session_1").run(
                DurableRunCommand(
                    waiting.run_id,
                    "approval_command_1",
                    "hitl_answer",
                    {"answer": "approved"},
                )
            )
        )

    assert result == AgentResult("approved")
    assert len(provider.requests) == 2
