from __future__ import annotations

from collections.abc import Callable, Iterator
import json

from agentos.channels.session import AgentSessionProvider
from agentos.channels.types import parse_channel_turn_request
from agentos.runtime import Agent
from agentos.runtime.stream_serializers import event_to_sse


class SseAgentChannel:
    """HTTP SSE 请求到 Agent stream 的适配器。"""

    def __init__(self, sessions: AgentSessionProvider) -> None:
        """创建 SSE channel。"""

        self._sessions = sessions

    def stream_turn(
        self,
        session_id: str,
        body: bytes | str,
        *,
        on_agent: Callable[[Agent], None] | None = None,
    ) -> Iterator[str]:
        """执行一个 turn，并产出 SSE chunks。"""

        try:
            request = parse_channel_turn_request(body)
        except ValueError as error:
            yield self._error_chunk(str(error))
            return

        agent = self._sessions.get_agent(session_id)
        try:
            if on_agent is not None:
                on_agent(agent)
            for event in agent.stream(
                request.message,
                thinking=request.thinking,
                show_thinking=request.show_thinking,
            ):
                chunk = event_to_sse(event, show_thinking=request.show_thinking)
                if chunk is not None:
                    yield chunk
        except Exception as error:
            yield self._error_chunk(str(error))
        finally:
            self._sessions.release_agent(session_id, agent)

    def _error_chunk(self, error: str) -> str:
        payload = json.dumps(
            {"type": "error", "status": "failed", "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"event: error\ndata: {payload}\n\n"
