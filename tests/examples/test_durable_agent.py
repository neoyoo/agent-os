from agentos.examples.durable_agent import build_durable_profile
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    AgentResult,
    AgentWaiting,
    DurableRunCommand,
)
from agentos.security import FernetPayloadProtector
from tests.planning._async import async_test


@async_test
async def test_durable_example_restarts_and_resumes_same_run(tmp_path) -> None:
    payload_protector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
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

    async with build_durable_profile(
        provider,
        tmp_path,
        payload_protector=payload_protector,
    ) as first:
        first_agent = await first.build_agent("session_1")
        waiting = await first_agent.run("submit")
        assert isinstance(waiting, AgentWaiting)

    async with build_durable_profile(
        provider,
        tmp_path,
        payload_protector=payload_protector,
    ) as restarted:
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

    assert result == AgentResult("approved")
    assert len(provider.requests) == 2
