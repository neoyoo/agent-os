from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from agentos.runtime.query_loop import QueryLoop
from agentos.runtime.stream_events import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    RunOptions,
    ToolStreamCompleted,
    ToolStreamStarted,
    TurnStreamCompleted,
    TurnStreamEvent,
)
from agentos.runtime.stream_serializers import event_to_json, event_to_sse


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Agent 完整响应结果。"""

    content: str


@dataclass(slots=True)
class Agent:
    """用户侧 agent facade，隐藏 QueryLoop 装配细节。"""

    query_loop: QueryLoop

    def __init__(
        self,
        query_loop: QueryLoop | None = None,
        query_loop_kwargs: dict[str, object] | None = None,
    ) -> None:
        """从 QueryLoop 或 QueryLoop kwargs 创建 Agent。"""

        if query_loop is None and query_loop_kwargs is None:
            raise ValueError("query_loop or query_loop_kwargs is required")
        self.query_loop = query_loop or QueryLoop(**query_loop_kwargs)  # type: ignore[arg-type]

    def run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        """运行完整 turn，并返回最终内容。"""

        final_content = ""
        for event in self.stream(
            user_message,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)

    def stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[TurnStreamEvent]:
        """运行 turn，并返回 typed stream events。"""

        yield from self.query_loop.run_turn_stream(
            user_message,
            RunOptions(thinking=thinking, show_thinking=show_thinking),
        )

    def stream_jsonl(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[str]:
        """运行 turn，并返回 JSONL 字符串。"""

        for event in self.stream(
            user_message,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            chunk = event_to_json(event, show_thinking=show_thinking)
            if chunk is not None:
                yield f"{chunk}\n"

    def stream_sse(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[str]:
        """运行 turn，并返回 SSE 字符串。"""

        for event in self.stream(
            user_message,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            chunk = event_to_sse(event, show_thinking=show_thinking)
            if chunk is not None:
                yield chunk

    def run_with_callbacks(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
        on_event: Callable[[TurnStreamEvent], None] | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        on_thinking_delta: Callable[[str], None] | None = None,
        on_tool_started: Callable[[str, str], None] | None = None,
        on_tool_completed: Callable[[str, str, str], None] | None = None,
    ) -> AgentResult:
        """运行 turn，并把 typed event 分发给 callback。"""

        final_content = ""
        for event in self.stream(
            user_message,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            if on_event is not None:
                on_event(event)
            if isinstance(event, AssistantContentDelta) and on_content_delta:
                on_content_delta(event.text)
            elif isinstance(event, AssistantThinkingDelta) and on_thinking_delta:
                on_thinking_delta(event.text)
            elif isinstance(event, ToolStreamStarted) and on_tool_started:
                on_tool_started(event.tool_name, event.tool_call_id)
            elif isinstance(event, ToolStreamCompleted) and on_tool_completed:
                on_tool_completed(
                    event.tool_name,
                    event.tool_call_id,
                    event.content,
                )
            elif isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)
