from __future__ import annotations

from typing import cast

from agentos.runtime import Agent, AgentResult
from tests.multi.helpers import build_agent_with_response


class RecordingProvider:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.released: list[tuple[str, Agent]] = []

    def get_agent(self, session_id: str) -> Agent:
        return self.agent

    def release_agent(self, session_id: str, agent: Agent) -> None:
        self.released.append((session_id, agent))


class FailingAgent:
    def run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        raise RuntimeError("provider unavailable")


def test_http_channel_runs_json_turn_and_releases_agent() -> None:
    from agentos.channels.http import HttpAgentChannel

    agent = build_agent_with_response("hello from agent")
    provider = RecordingProvider(agent)
    channel = HttpAgentChannel(provider)

    result = channel.handle_turn(
        "session_1",
        b'{"message":"hello","thinking":true,"show_thinking":false}',
    )

    assert result.status_code == 200
    assert result.session_id == "session_1"
    assert result.status == "completed"
    assert result.content == "hello from agent"
    assert provider.released == [("session_1", agent)]


def test_http_channel_rejects_invalid_json() -> None:
    from agentos.channels.http import HttpAgentChannel

    channel = HttpAgentChannel(RecordingProvider(build_agent_with_response("unused")))

    result = channel.handle_turn("session_1", b"{")

    assert result.status_code == 400
    assert result.status == "failed"
    assert "invalid JSON" in (result.error or "")


def test_http_channel_rejects_missing_message() -> None:
    from agentos.channels.http import HttpAgentChannel

    channel = HttpAgentChannel(RecordingProvider(build_agent_with_response("unused")))

    result = channel.handle_turn("session_1", b'{"thinking":true}')

    assert result.status_code == 400
    assert result.status == "failed"
    assert "message" in (result.error or "")


def test_http_channel_maps_agent_exception_to_failed_result() -> None:
    from agentos.channels.http import HttpAgentChannel

    agent = cast(Agent, FailingAgent())
    provider = RecordingProvider(agent)
    channel = HttpAgentChannel(provider)

    result = channel.handle_turn("session_1", b'{"message":"hello"}')

    assert result.status_code == 500
    assert result.status == "failed"
    assert result.error == "provider unavailable"
    assert provider.released == [("session_1", agent)]
