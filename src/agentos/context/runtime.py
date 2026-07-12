from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agentos.context.models import ContextProtocolError
from agentos.context.schema import (
    WorkingStateField,
    WorkingStateSchema,
    validate_working_state_fields,
    validate_working_state_value,
)
from agentos.context.state import (
    CompressedSegment,
    ContextState,
    WorkingStateValue,
    freeze_working_state_value,
)

if TYPE_CHECKING:
    from agentos.runtime.event_bus import EventBus
else:
    EventBus = object


@dataclass(slots=True)
class ContextRuntime:
    """执行 context protocol tools，并维护 ContextState。"""

    state: ContextState = field(default_factory=ContextState)
    event_bus: EventBus | None = None
    session_id: str | None = None
    turn_id: str | None = None

    def declare_schema(self, fields: list[WorkingStateField]) -> None:
        """声明当前 chapter 的 working state schema。"""

        if self.state.working_state_schema.fields:
            raise ContextProtocolError(
                "working state schema already declared for this chapter",
            )
        validated_fields = self._validate_fields(fields)
        self.state._replace_working_state_schema(
            WorkingStateSchema(fields=validated_fields),
        )
        from agentos.runtime.event_bus import WorkingStateSchemaDeclaredEvent

        self._emit(
            WorkingStateSchemaDeclaredEvent(
                fields=tuple(field.name for field in validated_fields),
                **self._event_context(),
            ),
        )

    def update_state(self, field_name: str, value: WorkingStateValue) -> None:
        """更新一个已声明的 working state 字段。"""

        declared_fields = self._declared_fields()
        if not declared_fields:
            raise ContextProtocolError("declare schema before updating working state")
        if type(field_name) is not str or field_name not in declared_fields:
            raise ContextProtocolError("working state field not declared")
        frozen = freeze_working_state_value(value)
        validate_working_state_value(declared_fields[field_name].type, frozen)
        self.state._set_frozen_working_state_value(field_name, frozen)
        from agentos.runtime.event_bus import WorkingStateUpdatedEvent

        self._emit(
            WorkingStateUpdatedEvent(
                field_name=field_name,
                **self._event_context(),
            ),
        )

    def extend_schema(self, fields: list[WorkingStateField]) -> None:
        """向当前 chapter 的 schema 追加字段。"""

        existing_fields = self.state.working_state_schema.fields
        if not existing_fields:
            raise ContextProtocolError("declare schema before extending it")

        existing_names = {item.name for item in existing_fields}
        new_fields = self._validate_fields(fields)
        for item in new_fields:
            if item.name in existing_names:
                raise ContextProtocolError(
                    f"working state field already exists: {item.name}",
                )

        self.state._replace_working_state_schema(
            WorkingStateSchema(
                fields=[*existing_fields, *new_fields],
            ),
        )
        from agentos.runtime.event_bus import WorkingStateSchemaExtendedEvent

        self._emit(
            WorkingStateSchemaExtendedEvent(
                fields=tuple(field.name for field in new_fields),
                **self._event_context(),
            ),
        )

    def start_chapter(self, fields: list[WorkingStateField] | None = None) -> None:
        """开启新 chapter，并重置 M2 working state。"""

        next_fields = [] if fields is None else self._validate_fields(fields)
        self.state._replace_working_state_schema(
            WorkingStateSchema(fields=next_fields),
        )
        self.state.clear_working_state()
        from agentos.runtime.event_bus import ChapterStartedEvent

        self._emit(
            ChapterStartedEvent(
                fields=tuple(field.name for field in next_fields),
                **self._event_context(),
            ),
        )

    def append_compressed_segment(self, segment: CompressedSegment) -> None:
        """追加 LLM 可见的压缩历史片段。"""

        self.state.append_compressed_segment(segment)
        from agentos.runtime.event_bus import CompressedSegmentAppendedEvent

        self._emit(
            CompressedSegmentAppendedEvent(
                segment_id=segment.id,
                **self._event_context(),
            ),
        )

    def set_inherited_state(self, items: list[str]) -> None:
        """设置跨 chapter 继承状态投影。"""

        self.state.set_inherited_state(items)
        from agentos.runtime.event_bus import InheritedStateSetEvent

        self._emit(
            InheritedStateSetEvent(
                item_count=len(items),
                **self._event_context(),
            ),
        )

    def set_memory_context(self, items: list[str]) -> None:
        """设置跨 session memory context 投影。"""

        self.state.set_memory_context(items)
        from agentos.runtime.event_bus import MemoryContextSetEvent

        self._emit(
            MemoryContextSetEvent(
                item_count=len(items),
                **self._event_context(),
            ),
        )

    def set_runtime_notices(self, notices: tuple[str, ...]) -> None:
        """设置本轮 provider request 可见的一次性 runtime notice。"""

        self.state.set_runtime_notices(notices)

    def clear_runtime_notices(self) -> None:
        """清空一次性 runtime notice。"""

        self.state.clear_runtime_notices()

    def snapshot(self) -> ContextState:
        """返回 request 构建使用的 ContextState 快照。"""

        return ContextState(
            working_state_schema=self.state.working_state_schema,
            working_state=self.state.working_state,
            compressed_history=list(self.state.compressed_history),
            inherited_state=list(self.state.inherited_state),
            memory_context=list(self.state.memory_context),
            runtime_notices=list(self.state.runtime_notices),
        )

    def _declared_fields(self) -> dict[str, WorkingStateField]:
        """返回已通过协议校验的字段名称映射。"""

        fields = validate_working_state_fields(
            self.state.working_state_schema.fields,
            allow_empty=True,
        )
        return {item.name: item for item in fields}

    def _emit(self, event: object) -> None:
        """向 EventBus 写入 context event。"""

        if self.event_bus is not None:
            self.event_bus.emit(event)

    def _event_context(self) -> dict[str, str | None]:
        """返回 context event 使用的 session/turn id。"""

        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
        }

    def _validate_fields(
        self,
        fields: list[WorkingStateField],
    ) -> tuple[WorkingStateField, ...]:
        """校验 schema 字段并保留输入顺序。"""

        return validate_working_state_fields(fields)
