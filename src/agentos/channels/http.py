from __future__ import annotations

from agentos.channels.session import AgentSessionProvider
from agentos.channels.types import ChannelTurnResult, parse_channel_turn_request


class HttpAgentChannel:
    """HTTP JSON 请求到 Agent turn 的适配器。"""

    def __init__(self, sessions: AgentSessionProvider) -> None:
        """创建 HTTP channel。"""

        self._sessions = sessions

    def handle_turn(self, session_id: str, body: bytes | str) -> ChannelTurnResult:
        """执行一个 JSON turn 请求。"""

        try:
            request = parse_channel_turn_request(body)
        except ValueError as error:
            return self._failed(session_id, str(error), status_code=400)

        agent = self._sessions.get_agent(session_id)
        try:
            result = agent.run(
                request.message,
                thinking=request.thinking,
                show_thinking=request.show_thinking,
            )
            return ChannelTurnResult(
                session_id=session_id,
                status="completed",
                content=result.content,
                status_code=200,
            )
        except Exception as error:
            return self._failed(session_id, str(error), status_code=500)
        finally:
            self._sessions.release_agent(session_id, agent)

    def _failed(
        self,
        session_id: str,
        error: str,
        *,
        status_code: int,
    ) -> ChannelTurnResult:
        return ChannelTurnResult(
            session_id=session_id,
            status="failed",
            error=error,
            status_code=status_code,
        )
