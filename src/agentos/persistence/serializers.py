from __future__ import annotations

from typing import Any

from agentos.compression.index import CompressionIndex
from agentos.context.schema import WorkingStateField, WorkingStateSchema
from agentos.context.state import CompressedSegment, ContextState
from agentos.messages.runtime import MessageRuntime
from agentos.messages.store import MessageStore
from agentos.messages.types import Message, MessageRef, ToolCall
from agentos.messages.window import ActiveWindow
from agentos.observability.events import event_record_from_dict, event_record_to_dict
from agentos.persistence.base import (
    SNAPSHOT_VERSION,
    SessionSnapshot,
    SnapshotVersionError,
)
from agentos.runtime.session import SessionState


JsonDict = dict[str, Any]


def working_state_schema_to_dict(schema: WorkingStateSchema) -> JsonDict:
    """序列化 working state schema。"""

    return {
        "fields": [
            {
                "name": field.name,
                "type": field.type,
                "purpose": field.purpose,
            }
            for field in schema.fields
        ],
    }


def working_state_schema_from_dict(data: JsonDict) -> WorkingStateSchema:
    """反序列化 working state schema。"""

    return WorkingStateSchema(
        fields=[
            WorkingStateField(
                name=str(field["name"]),
                type=str(field["type"]),
                purpose=str(field["purpose"]),
            )
            for field in data.get("fields", [])
        ],
    )


def context_state_to_dict(state: ContextState) -> JsonDict:
    """序列化 ContextState。"""

    return {
        "working_state_schema": working_state_schema_to_dict(
            state.working_state_schema,
        ),
        "working_state": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in state.working_state.items()
        },
        "compressed_history": [
            {
                "id": segment.id,
                "topic": segment.topic,
                "summary": segment.summary,
            }
            for segment in state.compressed_history
        ],
        "inherited_state": list(state.inherited_state),
        "memory_context": list(state.memory_context),
    }


def context_state_from_dict(data: JsonDict) -> ContextState:
    """反序列化 ContextState。"""

    return ContextState(
        working_state_schema=working_state_schema_from_dict(
            data.get("working_state_schema", {}),
        ),
        working_state=data.get("working_state", {}),
        compressed_history=[
            CompressedSegment(
                id=str(segment["id"]),
                topic=str(segment["topic"]),
                summary=str(segment["summary"]),
            )
            for segment in data.get("compressed_history", [])
        ],
        inherited_state=[str(item) for item in data.get("inherited_state", [])],
        memory_context=[str(item) for item in data.get("memory_context", [])],
    )


def tool_call_to_dict(tool_call: ToolCall) -> JsonDict:
    """序列化 assistant tool call。"""

    return {
        "id": tool_call.id,
        "name": tool_call.name,
        "arguments": dict(tool_call.arguments),
    }


def tool_call_from_dict(data: JsonDict) -> ToolCall:
    """反序列化 assistant tool call。"""

    return ToolCall(
        id=str(data["id"]),
        name=str(data["name"]),
        arguments=dict(data.get("arguments", {})),
    )


def message_to_dict(message: Message) -> JsonDict:
    """序列化原始 message。"""

    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "tool_calls": [
            tool_call_to_dict(tool_call) for tool_call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
    }


def message_from_dict(data: JsonDict) -> Message:
    """反序列化原始 message。"""

    return Message(
        id=str(data["id"]),
        role=data["role"],
        content=str(data["content"]),
        tool_calls=[
            tool_call_from_dict(tool_call)
            for tool_call in data.get("tool_calls", [])
        ],
        tool_call_id=(
            None
            if data.get("tool_call_id") is None
            else str(data.get("tool_call_id"))
        ),
    )


def message_runtime_to_dict(runtime: MessageRuntime) -> JsonDict:
    """序列化 MessageRuntime。"""

    return {
        "store": {
            "next_id": runtime.store.next_id_number(),
            "messages": [
                message_to_dict(message) for message in runtime.store.all()
            ],
        },
        "active_window": {
            "refs": [
                {
                    "message_id": ref.message_id,
                    "temporary": ref.temporary,
                }
                for ref in runtime.active_window.snapshot_refs()
            ],
        },
    }


def message_runtime_from_dict(data: JsonDict) -> MessageRuntime:
    """反序列化 MessageRuntime。"""

    store_data = data.get("store", {})
    window_data = data.get("active_window", {})
    store = MessageStore.from_messages(
        [message_from_dict(message) for message in store_data.get("messages", [])],
        next_id=int(store_data.get("next_id", 1)),
    )
    window = ActiveWindow.from_refs(
        [
            MessageRef(
                message_id=str(ref["message_id"]),
                temporary=bool(ref.get("temporary", False)),
            )
            for ref in window_data.get("refs", [])
        ],
    )
    return MessageRuntime.from_parts(store=store, active_window=window)


def compression_index_to_dict(index: CompressionIndex) -> JsonDict:
    """序列化 CompressionIndex。"""

    return {
        segment_id: list(source_refs)
        for segment_id, source_refs in index.snapshot().items()
    }


def compression_index_from_dict(data: JsonDict) -> CompressionIndex:
    """反序列化 CompressionIndex。"""

    return CompressionIndex.from_snapshot(
        {
            str(segment_id): [str(message_id) for message_id in source_refs]
            for segment_id, source_refs in data.items()
        },
    )


def session_state_to_dict(state: SessionState) -> JsonDict:
    """序列化 SessionState。"""

    return {
        "id": state.id,
        "status": state.status,
        "next_turn_number": state.next_turn_number(),
    }


def session_state_from_dict(data: JsonDict) -> SessionState:
    """反序列化 SessionState。"""

    return SessionState.from_snapshot(
        id=str(data["id"]),
        status=data.get("status", "new"),
        next_turn_number=int(data.get("next_turn_number", 1)),
    )


def session_snapshot_to_dict(snapshot: SessionSnapshot) -> JsonDict:
    """序列化完整 session snapshot。"""

    return {
        "version": snapshot.version,
        "session_state": session_state_to_dict(snapshot.session_state),
        "context_state": context_state_to_dict(snapshot.context_state),
        "message_runtime": message_runtime_to_dict(snapshot.message_runtime),
        "compression_index": compression_index_to_dict(snapshot.compression_index),
        "next_segment_number": snapshot.next_segment_number,
        "event_records": [
            event_record_to_dict(record)
            for record in snapshot.event_records
        ],
    }


def session_snapshot_from_dict(data: JsonDict) -> SessionSnapshot:
    """反序列化完整 session snapshot。"""

    version = int(data.get("version", 0))
    if version != SNAPSHOT_VERSION:
        raise SnapshotVersionError(f"unsupported snapshot version: {version}")

    return SessionSnapshot(
        session_state=session_state_from_dict(data["session_state"]),
        context_state=context_state_from_dict(data["context_state"]),
        message_runtime=message_runtime_from_dict(data["message_runtime"]),
        compression_index=compression_index_from_dict(data["compression_index"]),
        next_segment_number=int(data.get("next_segment_number", 1)),
        event_records=tuple(
            event_record_from_dict(record)
            for record in data.get("event_records", [])
        ),
        version=version,
    )
