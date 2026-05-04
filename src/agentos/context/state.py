from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from agentos.context.schema import WorkingStateSchema


WorkingStateValue = str | list[str] | tuple[str, ...]
WorkingStateSnapshot = Mapping[str, str | tuple[str, ...]]
CompressedHistorySnapshot = tuple["CompressedSegment", ...]
StringProjectionSnapshot = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompressedSegment:
    """LLM 可见的压缩历史片段。"""

    id: str
    topic: str
    summary: str


@dataclass(slots=True, init=False)
class ContextState:
    """由 context 包持有并渲染进默认 prompt 的状态。"""

    working_state_schema: WorkingStateSchema
    _working_state: dict[str, str | tuple[str, ...]]
    _compressed_history: list[CompressedSegment]
    _inherited_state: list[str]
    _memory_context: list[str]

    def __init__(
        self,
        working_state_schema: WorkingStateSchema | None = None,
        working_state: Mapping[str, WorkingStateValue] | None = None,
        compressed_history: Sequence[CompressedSegment] | None = None,
        inherited_state: Sequence[str] | None = None,
        memory_context: Sequence[str] | None = None,
    ) -> None:
        """创建 context state，并冻结对外暴露的投影。"""

        self.working_state_schema = working_state_schema or WorkingStateSchema()
        self._working_state = {}
        for key, value in (working_state or {}).items():
            self.set_working_state_value(key, value)
        self._compressed_history = list(compressed_history or [])
        self._inherited_state = list(inherited_state or [])
        self._memory_context = list(memory_context or [])

    @property
    def working_state(self) -> WorkingStateSnapshot:
        """返回不可变 working state 快照，禁止外部直接写入。"""

        return MappingProxyType(dict(self._working_state))

    @property
    def compressed_history(self) -> CompressedHistorySnapshot:
        """返回不可变 compressed history 投影，禁止外部直接写入。"""

        return tuple(self._compressed_history)

    @property
    def inherited_state(self) -> StringProjectionSnapshot:
        """返回不可变 inherited state 投影，禁止外部直接写入。"""

        return tuple(self._inherited_state)

    @property
    def memory_context(self) -> StringProjectionSnapshot:
        """返回不可变 memory context 投影，禁止外部直接写入。"""

        return tuple(self._memory_context)

    def set_working_state_value(
        self,
        field_name: str,
        value: WorkingStateValue,
    ) -> None:
        """由 ContextRuntime 写入单个 working state 字段。"""

        self._working_state[field_name] = self._coerce_working_state_value(value)

    def clear_working_state(self) -> None:
        """由 ContextRuntime 清空当前 chapter 的 working state。"""

        self._working_state.clear()

    def append_compressed_segment(self, segment: CompressedSegment) -> None:
        """由 ContextRuntime 追加 compressed history 片段。"""

        self._compressed_history.append(segment)

    def set_inherited_state(self, items: Sequence[str]) -> None:
        """由 ContextRuntime 替换 inherited state 投影。"""

        self._inherited_state = list(items)

    def set_memory_context(self, items: Sequence[str]) -> None:
        """由 ContextRuntime 替换 memory context 投影。"""

        self._memory_context = list(items)

    def _coerce_working_state_value(
        self,
        value: WorkingStateValue,
    ) -> str | tuple[str, ...]:
        """复制并冻结 working state 字段值。"""

        if isinstance(value, str):
            return value
        return tuple(value)
