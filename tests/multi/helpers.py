from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import Agent, ProviderRequestBuilder
from agentos.sync import SyncAgent
from tests._context_protocol_fixtures import default_context_renderer


_SYNC_AGENTS: list[SyncAgent] = []


def build_agent_with_response(content: str) -> Agent:
    """构造返回固定内容的测试 Agent。"""

    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": FakeProvider([ProviderResponse(content=content)]),
        },
    )


def track_sync_agent(agent: SyncAgent) -> SyncAgent:
    """Register a SyncAgent for deterministic test cleanup."""

    _SYNC_AGENTS.append(agent)
    return agent


def build_sync_agent_with_response(content: str) -> SyncAgent:
    """Build a tracked SyncAgent with one deterministic provider response."""

    return track_sync_agent(SyncAgent(build_agent_with_response(content)))


def close_tracked_sync_agents() -> None:
    """Close all SyncAgent instances created by multi-agent tests."""

    while _SYNC_AGENTS:
        _SYNC_AGENTS.pop().close()
