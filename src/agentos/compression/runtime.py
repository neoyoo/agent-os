from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from agentos.compression.compressor import Compressor, RuleBasedCompressor
from agentos.compression.evictor import Evictor
from agentos.compression.index import CompressionIndex
from agentos.context import CompressedSegment
from agentos.messages import MessageRuntime
from agentos.policies import BudgetPolicy

if TYPE_CHECKING:
    from agentos.runtime.event_bus import EventBus
else:
    EventBus = object


class CompressionContextBoundary(Protocol):
    """CompressionRuntime 依赖的 context runtime 边界。"""

    def append_compressed_segment(self, segment: CompressedSegment) -> None:
        """把压缩片段追加到 context 投影。"""


@dataclass(slots=True, init=False)
class CompressionRuntime:
    """协调 MessageRuntime、context 边界和压缩索引。"""

    context_runtime: CompressionContextBoundary
    message_runtime: MessageRuntime
    budget_policy: BudgetPolicy
    compressor: Compressor = field(default_factory=RuleBasedCompressor)
    index: CompressionIndex = field(default_factory=CompressionIndex)
    evictor: Evictor | None = None
    event_bus: EventBus | None = None
    session_id: str | None = None
    turn_id: str | None = None
    _next_segment_number: int = field(init=False)

    def __init__(
        self,
        context_runtime: CompressionContextBoundary,
        message_runtime: MessageRuntime,
        budget_policy: BudgetPolicy,
        compressor: Compressor | None = None,
        index: CompressionIndex | None = None,
        evictor: Evictor | None = None,
        event_bus: EventBus | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        next_segment_number: int = 1,
    ) -> None:
        """创建 compression runtime，并允许从 snapshot 恢复 segment cursor。"""

        self.context_runtime = context_runtime
        self.message_runtime = message_runtime
        self.budget_policy = budget_policy
        self.compressor = compressor if compressor is not None else RuleBasedCompressor()
        self.index = index if index is not None else CompressionIndex()
        self.evictor = evictor if evictor is not None else Evictor(budget_policy)
        self.event_bus = event_bus
        self.session_id = session_id
        self.turn_id = turn_id
        self._next_segment_number = next_segment_number

    def maybe_compress(self) -> CompressedSegment | None:
        """如果 active window 超过预算，就执行一次压缩。"""

        if self.message_runtime.has_temporary_recalled():
            self._emit_compression_skipped("temporary_recalled_refs")
            return None

        active_messages = self.message_runtime.materialize_active()
        selected_message_ids = self.evictor.select_message_ids(active_messages)
        if not selected_message_ids:
            self._emit_compression_skipped("under_budget")
            return None
        if len(selected_message_ids) >= len(active_messages):
            self._emit_compression_skipped("would_clear_window")
            return None

        source_messages = [
            self.message_runtime.store.get(message_id)
            for message_id in selected_message_ids
        ]
        segment = self.compressor.compress(self._next_segment_id(), source_messages)

        self.context_runtime.append_compressed_segment(segment)
        self.index.record(segment.id, selected_message_ids)
        self.message_runtime.active_window.remove_refs(
            selected_message_ids,
            self.message_runtime.store,
        )
        self._next_segment_number += 1
        from agentos.runtime.event_bus import CompressionCompletedEvent

        self._emit(
            CompressionCompletedEvent(
                segment_id=segment.id,
                source_message_ids=tuple(selected_message_ids),
                **self._event_context(),
            ),
        )
        return segment

    def next_segment_number(self) -> int:
        """返回下一次压缩会使用的 segment 序号。"""

        return self._next_segment_number

    def _emit_compression_skipped(self, reason: str) -> None:
        """发出 compression skipped event。"""

        from agentos.runtime.event_bus import CompressionSkippedEvent

        self._emit(
            CompressionSkippedEvent(
                reason=reason,
                **self._event_context(),
            ),
        )

    def _next_segment_id(self) -> str:
        """生成 LLM 可见的稳定 segment handle。"""

        return f"seg_{self._next_segment_number}"

    def _emit(self, event: object) -> None:
        """向 EventBus 写入 compression event。"""

        if self.event_bus is not None:
            self.event_bus.emit(event)

    def _event_context(self) -> dict[str, str | None]:
        """返回 compression event 使用的 session/turn id。"""

        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
        }
