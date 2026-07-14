from __future__ import annotations

from agentos.channels.durable_session import SessionLeaseError
from agentos.channels.session import AgentSessionProvider
from agentos.channels.turn_execution import run_channel_agent
from agentos.channels.types import ChannelTurnResult, parse_channel_turn_request
from agentos.persistence import BackendUnavailableError
from agentos.runtime import AgentWaiting, RunOptions


class HttpAgentChannel:
    """HTTP JSON 请求到 Agent turn 的适配器。"""

    def __init__(
        self,
        sessions: AgentSessionProvider,
        *,
        expose_internal_errors: bool = False,
    ) -> None:
        """创建 HTTP channel。"""

        self._sessions = sessions
        self._expose_internal_errors = expose_internal_errors

    async def handle_turn(
        self,
        session_id: str,
        body: bytes | str,
    ) -> ChannelTurnResult:
        """执行一个 JSON turn 请求。"""

        try:
            request = parse_channel_turn_request(body)
        except ValueError as error:
            return self._failed(session_id, str(error), status_code=400)

        try:
            result = await run_channel_agent(
                self._sessions,
                session_id,
                request.message,
                options=RunOptions(
                    thinking=request.thinking,
                    show_thinking=request.show_thinking,
                ),
            )
            if isinstance(result, AgentWaiting):
                return ChannelTurnResult(
                    session_id=session_id,
                    status="waiting",
                    run_id=result.run_id,
                    wait_reason=result.reason,
                    status_code=202,
                )
            return ChannelTurnResult(
                session_id=session_id,
                status="completed",
                content=result.content,
                status_code=200,
            )
        except SessionLeaseError as error:
            return self._failed(
                session_id,
                self._public_error_message(error),
                status_code=423,
            )
        except BackendUnavailableError as error:
            return self._failed(
                session_id,
                self._public_error_message(error),
                status_code=503,
            )
        except Exception as error:
            return self._failed(
                session_id,
                self._public_error_message(error),
                status_code=500,
            )

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

    def _public_error_message(self, error: BaseException) -> str:
        if self._expose_internal_errors:
            return str(error)
        if isinstance(error, SessionLeaseError):
            return "session unavailable"
        if isinstance(error, BackendUnavailableError):
            return "backend unavailable"
        return "internal error"
