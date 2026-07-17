from agentos.builder import AgentBuilder
from agentos.providers import (
    FakeProvider,
    Provider,
    ProviderResponse,
    ProviderToolCall,
)
from agentos.runtime import Agent, AgentResult
from agentos.sync import SyncAgent


def build_agent(provider: Provider) -> Agent:
    """通过 Builder 构建启用默认 Context Protocol 的 Level 1 Agent。"""

    return AgentBuilder().provider(provider).build(
        session_id="session_context_protocol_example"
    )


def _demo_provider() -> FakeProvider:
    return FakeProvider(
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


def main() -> int:
    """在无外部服务环境运行 Context Protocol 示例。"""

    with SyncAgent(build_agent(_demo_provider())) as agent:
        result = agent.run("记录当前任务目标")
    if not isinstance(result, AgentResult):
        raise RuntimeError("context protocol example unexpectedly entered waiting state")
    print(result.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
