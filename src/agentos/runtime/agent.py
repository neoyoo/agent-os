from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, fields
from threading import RLock
from typing import AsyncIterator

from agentos.runtime._async_bridge import iterate_sync_in_executor
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


class _AgentAsyncStream:
    """Agent async stream 适配器，负责维护 interrupt/cancel 状态。"""

    def __init__(
        self,
        agent: "Agent",
        stream: AsyncIterator[TurnStreamEvent],
    ) -> None:
        self._agent = agent
        self._stream = stream
        self._task: asyncio.Task[object] | None = None

    def __aiter__(self) -> "_AgentAsyncStream":
        return self

    async def __anext__(self) -> TurnStreamEvent:
        self._set_current_task()
        try:
            return await self._stream.__anext__()
        except StopAsyncIteration:
            self._clear_current_task()
            raise
        except asyncio.CancelledError:
            self._agent.query_loop.request_interrupt()
            raise

    async def aclose(self) -> None:
        self._set_current_task()
        try:
            aclose = getattr(self._stream, "aclose", None)
            if callable(aclose):
                await aclose()
        finally:
            self._clear_current_task()

    def _set_current_task(self) -> None:
        self._task = asyncio.current_task()
        self._agent._current_async_task = self._task

    def _clear_current_task(self) -> None:
        if self._agent._current_async_task is self._task:
            self._agent._current_async_task = None


@dataclass(slots=True)
class Agent:
    """用户侧 agent facade，隐藏 QueryLoop 装配细节。"""

    query_loop: QueryLoop
    _turn_lock: RLock
    _current_async_task: asyncio.Task[object] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __init__(
        self,
        query_loop: QueryLoop | None = None,
        query_loop_kwargs: dict[str, object] | None = None,
    ) -> None:
        """从 QueryLoop 或 QueryLoop kwargs 创建 Agent。"""

        self._turn_lock = RLock()
        self._current_async_task = None
        if query_loop is None and query_loop_kwargs is None:
            raise ValueError("query_loop or query_loop_kwargs is required")
        if query_loop is not None:
            self.query_loop = query_loop
            return

        kwargs = dict(query_loop_kwargs or {})
        allowed_keys = {field.name for field in fields(QueryLoop) if field.init}
        unknown_keys = sorted(set(kwargs) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                "unknown query_loop_kwargs: " + ", ".join(unknown_keys),
            )
        try:
            self.query_loop = QueryLoop(**kwargs)
        except TypeError as error:
            raise ValueError(f"invalid query_loop_kwargs: {error}") from error

    @property
    def interrupted(self) -> bool:
        """判断底层 QueryLoop 是否已收到中断请求。"""

        return self.query_loop.interrupted

    @property
    def attachments(self) -> object:
        """返回当前 Agent 配置的 AttachmentRuntime。"""

        attachment_runtime = getattr(
            self.query_loop.request_builder,
            "attachment_runtime",
            None,
        )
        if attachment_runtime is None:
            raise RuntimeError("attachment runtime is not configured")
        return attachment_runtime

    def interrupt(self) -> None:
        """请求在下一个安全点中断运行。"""

        self.query_loop.request_interrupt()
        if self._current_async_task is not None:
            self._current_async_task.cancel()

    def clear_interrupt(self) -> None:
        """清除中断请求。"""

        self.query_loop.clear_interrupt()

    def run(
        self,
        user_message: str,
        *,
        attachments: list[object] | None = None,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        """运行完整 turn，并返回最终内容。"""

        final_content = ""
        for event in self.stream(
            user_message,
            attachments=attachments,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)

    async def async_run(
        self,
        user_message: str,
        *,
        attachments: list[object] | None = None,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        """异步运行完整 turn，并返回最终内容。"""

        final_content = ""
        async for event in self.async_stream(
            user_message,
            attachments=attachments,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)

    def async_stream(
        self,
        user_message: str,
        *,
        attachments: list[object] | None = None,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 turn，v1 用 executor 包装同步 stream。"""

        return _AgentAsyncStream(
            self,
            self._stream_sync_in_executor(
                lambda: self.stream(
                    user_message,
                    attachments=attachments,
                    thinking=thinking,
                    show_thinking=show_thinking,
                ),
            ),
        )

    def _stream_sync_in_executor(
        self,
        factory: Callable[[], Iterator[TurnStreamEvent]],
    ) -> AsyncIterator[TurnStreamEvent]:
        """在线程池中消费同步 stream，避免阻塞 asyncio event loop。"""

        before_start = getattr(
            self.query_loop,
            "set_async_provider_event_loop",
            None,
        )
        after_worker = getattr(
            self.query_loop,
            "clear_async_provider_event_loop",
            None,
        )
        return iterate_sync_in_executor(
            factory,
            on_cancel=self.query_loop.request_interrupt,
            before_start=before_start if callable(before_start) else None,
            after_worker=after_worker if callable(after_worker) else None,
        )

    def stream(
        self,
        user_message: str,
        *,
        attachments: list[object] | None = None,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[TurnStreamEvent]:
        """运行 turn，并返回 typed stream events。"""

        with self._turn_lock:
            run_options = RunOptions(thinking=thinking, show_thinking=show_thinking)
            if attachments is None:
                yield from self.query_loop.run_turn_stream(user_message, run_options)
            else:
                yield from self.query_loop.run_turn_stream(
                    user_message,
                    run_options,
                    attachments=attachments,
                )

    def run_continuation(
        self,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        """运行 runtime continuation turn，并返回最终内容。"""

        final_content = ""
        for event in self.stream_continuation(
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)

    def stream_continuation(
        self,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[TurnStreamEvent]:
        """运行 runtime continuation turn，不追加 user 消息。"""

        with self._turn_lock:
            yield from self.query_loop.run_continuation_stream(
                RunOptions(thinking=thinking, show_thinking=show_thinking),
            )

    def stream_jsonl(
        self,
        user_message: str,
        *,
        attachments: list[object] | None = None,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[str]:
        """运行 turn，并返回 JSONL 字符串。"""

        for event in self.stream(
            user_message,
            attachments=attachments,
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
        attachments: list[object] | None = None,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[str]:
        """运行 turn，并返回 SSE 字符串。"""

        for event in self.stream(
            user_message,
            attachments=attachments,
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
        attachments: list[object] | None = None,
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
            attachments=attachments,
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
