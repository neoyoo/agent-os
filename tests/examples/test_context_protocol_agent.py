import asyncio

from agentos.examples.context_protocol_agent import build_agent, main
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import AgentResult


def test_context_protocol_example_runs_builder_managed_context_tools() -> None:
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call_declare",
                        name="declare_schema",
                        arguments={
                            "fields": [
                                {
                                    "name": "task_goal",
                                    "type": "string",
                                    "purpose": "当前任务目标",
                                }
                            ]
                        },
                    ),
                )
            ),
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call_update",
                        name="update_state",
                        arguments={
                            "field_name": "task_goal",
                            "value": "演示 Context Protocol",
                        },
                    ),
                )
            ),
            ProviderResponse(content="上下文状态已更新。"),
        ]
    )
    agent = build_agent(provider)

    result = asyncio.run(agent.run("记录当前任务目标"))

    assert result == AgentResult("上下文状态已更新。")
    declared_snapshot = provider.requests[1].messages[0].content[0].text
    updated_snapshot = provider.requests[2].messages[0].content[0].text
    assert '<field name="task_goal"' in declared_snapshot
    assert "演示 Context Protocol" in updated_snapshot


def test_context_protocol_example_main_needs_no_external_service(capsys) -> None:
    assert main() == 0
    assert capsys.readouterr().out.strip() == "上下文状态已更新。"
