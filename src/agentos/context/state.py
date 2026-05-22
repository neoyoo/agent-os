from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence, TypeAlias

from agentos.context.schema import WorkingStateSchema


JsonScalar: TypeAlias = str | int | float | bool | None
WorkingStateValue: TypeAlias = (
    JsonScalar
    | list[object]
    | tuple[object, ...]
    | Mapping[str, object]
)
FrozenWorkingStateValue: TypeAlias = JsonScalar | tuple[object, ...] | Mapping[str, object]
WorkingStateSnapshot = Mapping[str, FrozenWorkingStateValue]
CompressedHistorySnapshot = tuple["CompressedSegment", ...]
StringProjectionSnapshot = tuple[str, ...]


class FrozenMapping(Mapping[str, object]):
    """递归冻结后的 JSON object working state 值。"""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, object]) -> None:
        self._data = MappingProxyType(dict(data))

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        return working_state_value_to_json(self) == working_state_value_to_json(other)

    def __repr__(self) -> str:
        return repr(dict(self._data))


@dataclass(frozen=True, slots=True)
class CompressedSegment:
    """LLM 可见的压缩历史片段。"""

    id: str
    topic: str
    summary: str


@dataclass(slots=True, init=False)
class ContextState:
    """由 context 包持有并渲染进默认 prompt 的状态。"""

    _working_state_schema: WorkingStateSchema
    _working_state: dict[str, FrozenWorkingStateValue]
    _compressed_history: list[CompressedSegment]
    _inherited_state: list[str]
    _memory_context: list[str]
    _runtime_notices: list[str]

    def __init__(
        self,
        working_state_schema: WorkingStateSchema | None = None,
        working_state: Mapping[str, WorkingStateValue] | None = None,
        compressed_history: Sequence[CompressedSegment] | None = None,
        inherited_state: Sequence[str] | None = None,
        memory_context: Sequence[str] | None = None,
        runtime_notices: Sequence[str] | None = None,
    ) -> None:
        """创建 context state，并冻结对外暴露的投影。"""

        self._working_state_schema = working_state_schema or WorkingStateSchema()
        self._working_state = {}
        for key, value in (working_state or {}).items():
            self.set_working_state_value(key, value)
        self._compressed_history = list(compressed_history or [])
        self._inherited_state = list(inherited_state or [])
        self._memory_context = list(memory_context or [])
        self._runtime_notices = list(runtime_notices or [])

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

    @property
    def runtime_notices(self) -> StringProjectionSnapshot:
        """返回不可变 runtime notice 投影，禁止外部直接写入。"""

        return tuple(self._runtime_notices)

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

    def set_runtime_notices(self, items: Sequence[str]) -> None:
        """由 ContextRuntime 替换一次性 runtime notice 投影。"""

        self._runtime_notices = list(items)

    def clear_runtime_notices(self) -> None:
        """由 ContextRuntime 清空一次性 runtime notice 投影。"""

        self._runtime_notices.clear()

    def _coerce_working_state_value(
        self,
        value: WorkingStateValue,
    ) -> FrozenWorkingStateValue:
        """复制并冻结 working state 字段值。"""

        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Mapping):
            return FrozenMapping(
                {
                    str(key): self._coerce_working_state_value(item)
                    for key, item in value.items()
                },
            )
        if isinstance(value, (list, tuple)):
            return tuple(self._coerce_working_state_value(item) for item in value)
        raise TypeError("working state value must be JSON-compatible")

    @property
    def working_state_schema(self) -> WorkingStateSchema:
        """返回当前 chapter 的不可替换 working state schema。"""

        return self._working_state_schema

    def _replace_working_state_schema(self, schema: WorkingStateSchema) -> None:
        """由 ContextRuntime 替换当前 chapter schema。"""

        self._working_state_schema = schema


def working_state_value_to_json(value: object) -> object:
    """把冻结后的 working state 值还原为 JSON-safe Python 值。"""

    if isinstance(value, FrozenMapping):
        return {
            key: working_state_value_to_json(item)
            for key, item in value.items()
        }
    if isinstance(value, Mapping):
        return {
            str(key): working_state_value_to_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [working_state_value_to_json(item) for item in value]
    if isinstance(value, list):
        return [working_state_value_to_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("working state value must be JSON-compatible")
