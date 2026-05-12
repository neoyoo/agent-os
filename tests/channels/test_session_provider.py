from __future__ import annotations

from agentos.runtime import Agent
from tests.multi.helpers import build_agent_with_response


def test_in_memory_session_provider_creates_and_reuses_agents() -> None:
    from agentos.channels.session import InMemoryAgentSessionProvider

    created_session_ids: list[str] = []

    def factory(session_id: str) -> Agent:
        created_session_ids.append(session_id)
        return build_agent_with_response(f"response for {session_id}")

    provider = InMemoryAgentSessionProvider(factory)

    first = provider.get_agent("session_1")
    again = provider.get_agent("session_1")
    second = provider.get_agent("session_2")

    assert first is again
    assert first is not second
    assert created_session_ids == ["session_1", "session_2"]


def test_release_agent_does_not_destroy_cached_session() -> None:
    from agentos.channels.session import InMemoryAgentSessionProvider

    provider = InMemoryAgentSessionProvider(
        lambda session_id: build_agent_with_response(session_id),
    )
    agent = provider.get_agent("session_1")

    provider.release_agent("session_1", agent)

    assert provider.get_agent("session_1") is agent
