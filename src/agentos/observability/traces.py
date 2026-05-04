from dataclasses import dataclass, field
import re
from typing import Protocol

from agentos.observability.events import EventRecord


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """由 EventRecord 归一化得到的 trace 记录。"""

    name: str
    session_id: str | None
    turn_id: str | None
    trace_id: str
    span_id: str
    attributes: dict[str, object] = field(default_factory=dict)


class TraceSink(Protocol):
    """trace 输出边界。"""

    def record_many(self, records: list[TraceRecord]) -> None:
        """输出一组 trace 记录。"""


class EventTraceProjector:
    """把 event log 转换成 provider/tool/context trace。"""

    _EVENT_NAMES = {
        "TurnStartedEvent": "turn.started",
        "TurnCompletedEvent": "turn.completed",
        "TurnFailedEvent": "turn.failed",
        "UserMessageAppendedEvent": "message.user.appended",
        "AssistantMessageAppendedEvent": "message.assistant.appended",
        "ToolResultAppendedEvent": "message.tool_result.appended",
        "ContextRenderedEvent": "context.rendered",
        "ProviderRequestBuiltEvent": "provider.request",
        "ProviderResponseReceivedEvent": "provider.response",
        "ToolCallRequestedEvent": "tool.requested",
        "CompressionSkippedEvent": "compression.skipped",
        "CompressionCompletedEvent": "compression.completed",
        "CompressedSegmentAppendedEvent": "compression.segment_appended",
        "RecallContextRequestedEvent": "recall.requested",
        "RecallContextFailedEvent": "recall.failed",
        "RecallContextInjectedEvent": "recall.injected",
        "WorkingStateSchemaDeclaredEvent": "context.schema_declared",
        "WorkingStateSchemaExtendedEvent": "context.schema_extended",
        "WorkingStateUpdatedEvent": "context.state_updated",
        "ChapterStartedEvent": "context.chapter_started",
        "InheritedStateSetEvent": "context.inherited_state_set",
        "MemoryContextSetEvent": "context.memory_context_set",
        "SnapshotSavedEvent": "persistence.snapshot_saved",
        "SnapshotLoadedEvent": "persistence.snapshot_loaded",
    }

    _PAYLOAD_ATTRIBUTE_NAMES = {
        "message_id": "message.id",
        "tool_name": "tool.name",
        "tool_call_id": "tool.call_id",
        "segment_id": "compression.id",
        "source_message_ids": "source.message_ids",
        "handle": "recall.handle",
        "message_ids": "message.ids",
        "fields": "schema.fields",
        "field_name": "state.field",
        "item_count": "item.count",
        "reason": "reason",
        "error": "error",
        "snapshot_session_id": "snapshot.session_id",
    }

    def project(
        self,
        records: list[EventRecord] | tuple[EventRecord, ...],
    ) -> list[TraceRecord]:
        """转换 runtime event records。"""

        traces: list[TraceRecord] = []
        for record in records:
            payload = record.payload
            trace_id = record.session_id or f"event-log-{record.sequence}"
            traces.append(
                TraceRecord(
                    name=self._trace_name(record),
                    session_id=record.session_id,
                    turn_id=record.turn_id,
                    trace_id=trace_id,
                    span_id=f"event-{record.sequence}",
                    attributes=self._attributes(record, payload),
                ),
            )
        return traces

    def _trace_name(self, record: EventRecord) -> str:
        """返回稳定 trace 名称。"""

        if record.event_type.startswith("ToolExecution"):
            return f"tool.{record.payload.get('tool_name', 'unknown')}"
        return self._EVENT_NAMES.get(record.event_type, self._fallback_name(record))

    def _fallback_name(self, record: EventRecord) -> str:
        """为未知事件生成稳定名称。"""

        name = record.event_type.removesuffix("Event")
        return "runtime." + re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    def _attributes(
        self,
        record: EventRecord,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """投影安全 metadata，不记录 prompt/message 内容。"""

        attributes: dict[str, object] = {"event.type": record.event_type}
        for payload_key, attribute_key in self._PAYLOAD_ATTRIBUTE_NAMES.items():
            if payload_key in payload:
                attributes[attribute_key] = payload[payload_key]
        return attributes
