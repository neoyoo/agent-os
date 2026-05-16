from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from agentos.runtime._async_bridge import iterate_sync_in_executor
from agentos.compression import CompressionRuntime
from agentos.messages import MessageRuntime
from agentos.providers import Provider
from agentos.runtime.provider_request_builder import ProviderRequestBuilder
from agentos.runtime.query_loop import (
    ContextRuntimeBoundary,
    QueryLoop,
    ToolCallRouterBoundary,
    TurnNoticeProvider,
)
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    RunOptions,
    TurnStreamCompleted,
    TurnStreamEvent,
)


@dataclass(slots=True)
class AsyncQueryLoop:
    """异步 agent turn 调度器，v1 用 executor 包装同步 QueryLoop。"""

    context_runtime: ContextRuntimeBoundary
    message_runtime: MessageRuntime
    request_builder: ProviderRequestBuilder
    provider: Provider
    compression_runtime: CompressionRuntime | None = None
    tool_call_router: ToolCallRouterBoundary | None = None
    event_bus: object | None = None
    hook_manager: object | None = None
    session_state: SessionState | None = None
    turn_notice_provider: TurnNoticeProvider | None = None
    max_tool_iterations: int = 8
    sync_loop: QueryLoop = field(init=False)

    def __post_init__(self) -> None:
        """构造内部同步 loop，保持 v1 行为与 QueryLoop 一致。"""

        self.sync_loop = QueryLoop(
            context_runtime=self.context_runtime,
            message_runtime=self.message_runtime,
            request_builder=self.request_builder,
            provider=self.provider,
            compression_runtime=self.compression_runtime,
            tool_call_router=self.tool_call_router,
            event_bus=self.event_bus,  # type: ignore[arg-type]
            hook_manager=self.hook_manager,  # type: ignore[arg-type]
            session_state=self.session_state,
            turn_notice_provider=self.turn_notice_provider,
            max_tool_iterations=self.max_tool_iterations,
        )

    async def run_turn(self, user_message: str) -> str:
        """异步运行完整 turn，返回最终内容。"""

        final_content = ""
        async for event in self.run_turn_stream(user_message):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return final_content

    def run_turn_stream(
        self,
        user_message: str,
        options: RunOptions | None = None,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 turn，产出 typed stream events。"""

        return iterate_sync_in_executor(
            lambda: self.sync_loop.run_turn_stream(user_message, options),
            on_cancel=self.sync_loop.request_interrupt,
            before_start=self.sync_loop.set_async_provider_event_loop,
            after_worker=self.sync_loop.clear_async_provider_event_loop,
        )

    def run_continuation_stream(
        self,
        options: RunOptions | None = None,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 runtime continuation turn。"""

        return iterate_sync_in_executor(
            lambda: self.sync_loop.run_continuation_stream(options),
            on_cancel=self.sync_loop.request_interrupt,
            before_start=self.sync_loop.set_async_provider_event_loop,
            after_worker=self.sync_loop.clear_async_provider_event_loop,
        )
