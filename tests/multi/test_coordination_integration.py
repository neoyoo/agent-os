import time

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.multi import (
    AgentCard,
    AgentCoordinationTools,
    AgentCoordinator,
    AgentInbox,
    ExpertAgentRunner,
    InMemoryRegistry,
    SpawnExecutor,
    TaskTable,
)
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import Agent, ProviderRequestBuilder
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


def build_parent_agent(
    coordinator: AgentCoordinator,
    responses: list[ProviderResponse],
) -> Agent:
    tool_registry = ToolRegistry()
    AgentCoordinationTools(
        coordinator=coordinator,
        parent_agent_id="parent",
    ).register(tool_registry)
    router = ToolCallRouter(tool_registry=tool_registry)
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=router.tool_specs(),
            ),
            "provider": FakeProvider(responses),
            "tool_call_router": router,
        },
    )


def build_coordinator() -> AgentCoordinator:
    return AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )


def wait_for_result(coordinator: AgentCoordinator):
    deadline = time.time() + 2
    while time.time() < deadline:
        results = coordinator.collect_results("parent")
        if results:
            return results[0]
        time.sleep(0.01)
    raise AssertionError("timed out waiting for coordination result")


def test_fake_provider_spawn_subagent_tool_end_to_end() -> None:
    coordinator = build_coordinator()
    parent = build_parent_agent(
        coordinator,
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_spawn",
                        name="spawn_subagent",
                        arguments={"instruction": "Review this"},
                    ),
                ],
            ),
            ProviderResponse(content="spawn submitted"),
        ],
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        parent,
    )

    result = parent.run("start spawn")
    task_result = wait_for_result(coordinator)

    assert result.content == "spawn submitted"
    assert task_result.status == "completed"
    assert task_result.summary == "child result"

    coordinator.spawn_executor.shutdown()


def test_fake_provider_dispatch_to_expert_tool_end_to_end() -> None:
    coordinator = build_coordinator()
    parent = build_parent_agent(
        coordinator,
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_dispatch",
                        name="dispatch_to_expert",
                        arguments={
                            "instruction": "Review Python",
                            "required_capabilities": ["python"],
                        },
                    ),
                ],
            ),
            ProviderResponse(content="dispatch submitted"),
        ],
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        parent,
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Python expert.",
            capabilities=("python",),
        ),
        build_agent_with_response("expert result"),
    )

    result = parent.run("start dispatch")
    runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")

    assert runner.run_once(timeout=0.1) is True
    assert result.content == "dispatch submitted"
    assert wait_for_result(coordinator).summary == "expert result"

    coordinator.spawn_executor.shutdown()
