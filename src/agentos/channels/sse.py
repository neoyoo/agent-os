from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import json

from agentos.channels.session import AgentSessionProvider
from agentos.channels.turn_execution import open_channel_agent_stream
from agentos.channels.types import parse_channel_turn_request
from agentos.runtime import (
    Agent,
    RunOptions,
    ToolStreamFailed,
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamWaiting,
    event_to_sse,
)


_BUFFERED_TERMINAL_EVENTS = (
    TurnStreamCompleted,
    TurnStreamWaiting,
    TurnStreamCancelled,
)


class SseAgentChannel:
    """HTTP SSE 请求到 Agent stream 的适配器。"""

    def __init__(
        self,
        sessions: AgentSessionProvider,
        *,
        expose_internal_errors: bool = False,
    ) -> None:
        """创建 SSE channel。"""

        self._sessions = sessions
        self._expose_internal_errors = expose_internal_errors

    async def stream_turn(
        self,
        session_id: str,
        body: bytes | str,
        *,
        on_agent: Callable[[Agent], None] | None = None,
    ) -> AsyncIterator[str]:
        """执行一个 turn，并产出 SSE chunks。"""

        try:
            request = parse_channel_turn_request(body)
        except ValueError as error:
            yield self._error_chunk(str(error))
            return

        terminal_chunk: str | None = None
        terminal_error: BaseException | None = None
        try:
            async with open_channel_agent_stream(
                self._sessions,
                session_id,
                request.message,
                options=RunOptions(
                    thinking=request.thinking,
                    show_thinking=request.show_thinking,
                ),
                on_agent=on_agent,
            ) as stream:
                async for event in stream:
                    if isinstance(event, TurnStreamFailed):
                        terminal_error = event.error
                        break
                    chunk = self._event_chunk(
                        event,
                        show_thinking=request.show_thinking,
                    )
                    if chunk is None:
                        continue
                    if isinstance(event, _BUFFERED_TERMINAL_EVENTS):
                        terminal_chunk = chunk
                        break
                    yield chunk
        except Exception as error:
            if terminal_error is None:
                terminal_error = error
            elif error is not terminal_error:
                terminal_error = RuntimeError(
                    f"{terminal_error}; release failed: {error}",
                )

        if terminal_error is not None:
            yield self._error_chunk(self._public_error_message(terminal_error))
        elif terminal_chunk is not None:
            yield terminal_chunk

    def _event_chunk(
        self,
        event: TurnStreamEvent,
        *,
        show_thinking: bool,
    ) -> str | None:
        if isinstance(event, ToolStreamFailed) and not self._expose_internal_errors:
            event = ToolStreamFailed(
                tool_name=event.tool_name,
                tool_call_id=event.tool_call_id,
                error=RuntimeError("internal error"),
            )
        return event_to_sse(event, show_thinking=show_thinking)

    def _public_error_message(self, error: BaseException) -> str:
        if self._expose_internal_errors:
            return str(error)
        return "internal error"

    def _error_chunk(self, error: str) -> str:
        payload = json.dumps(
            {"type": "error", "status": "failed", "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"event: error\ndata: {payload}\n\n"
