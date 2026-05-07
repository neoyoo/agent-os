from __future__ import annotations

from typing import Any

from agentos.context import CompressedSegment
from agentos.memory.types import (
    CompressedSegmentPackage,
    HotSessionState,
    SegmentRecallDocument,
)
from agentos.messages import Message, MessageRef, ToolCall


JsonDict = dict[str, Any]


def message_ref_to_dict(ref: MessageRef) -> JsonDict:
    """序列化 MessageRef。"""

    return {
        "message_id": ref.message_id,
        "temporary": ref.temporary,
    }


def message_ref_from_dict(data: JsonDict) -> MessageRef:
    """反序列化 MessageRef。"""

    return MessageRef(
        message_id=str(data["message_id"]),
        temporary=bool(data.get("temporary", False)),
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


def compressed_segment_to_dict(segment: CompressedSegment) -> JsonDict:
    """序列化 LLM 可见 compressed segment。"""

    return {
        "id": segment.id,
        "topic": segment.topic,
        "summary": segment.summary,
    }


def compressed_segment_from_dict(data: JsonDict) -> CompressedSegment:
    """反序列化 LLM 可见 compressed segment。"""

    return CompressedSegment(
        id=str(data["id"]),
        topic=str(data["topic"]),
        summary=str(data["summary"]),
    )


def recall_document_to_dict(document: SegmentRecallDocument) -> JsonDict:
    """序列化 recall document。"""

    return {
        "session_id": document.session_id,
        "segment_id": document.segment_id,
        "topic": document.topic,
        "summary": document.summary,
        "keywords": list(document.keywords),
        "tool_hints": list(document.tool_hints),
        "searchable_text": document.searchable_text,
    }


def recall_document_from_dict(data: JsonDict) -> SegmentRecallDocument:
    """反序列化 recall document。"""

    return SegmentRecallDocument(
        session_id=str(data["session_id"]),
        segment_id=str(data["segment_id"]),
        topic=str(data["topic"]),
        summary=str(data["summary"]),
        keywords=[str(item) for item in data.get("keywords", [])],
        tool_hints=[str(item) for item in data.get("tool_hints", [])],
        searchable_text=str(data.get("searchable_text", "")),
    )


def package_to_dict(package: CompressedSegmentPackage) -> JsonDict:
    """序列化 compression package。"""

    return {
        "segment": compressed_segment_to_dict(package.segment),
        "source_refs": list(package.source_refs),
        "recall_document": recall_document_to_dict(package.recall_document),
    }


def package_from_dict(data: JsonDict) -> CompressedSegmentPackage:
    """反序列化 compression package。"""

    return CompressedSegmentPackage(
        segment=compressed_segment_from_dict(data["segment"]),
        source_refs=[str(item) for item in data.get("source_refs", [])],
        recall_document=recall_document_from_dict(data["recall_document"]),
    )


def hot_state_to_dict(state: HotSessionState) -> JsonDict:
    """序列化 HotSessionState。"""

    return {
        "session_id": state.session_id,
        "active_refs": [message_ref_to_dict(ref) for ref in state.active_refs],
        "recent_messages": [
            message_to_dict(message) for message in state.recent_messages
        ],
        "temporary_recalled_refs": list(state.temporary_recalled_refs),
        "segment_refs": {
            segment_id: list(refs)
            for segment_id, refs in state.segment_refs.items()
        },
        "metadata": dict(state.metadata),
    }


def hot_state_from_dict(data: JsonDict) -> HotSessionState:
    """反序列化 HotSessionState。"""

    return HotSessionState(
        session_id=str(data["session_id"]),
        active_refs=[
            message_ref_from_dict(ref) for ref in data.get("active_refs", [])
        ],
        recent_messages=[
            message_from_dict(message)
            for message in data.get("recent_messages", [])
        ],
        temporary_recalled_refs=[
            str(ref) for ref in data.get("temporary_recalled_refs", [])
        ],
        segment_refs={
            str(segment_id): tuple(str(ref) for ref in refs)
            for segment_id, refs in data.get("segment_refs", {}).items()
        },
        metadata=dict(data.get("metadata", {})),
    )


__all__ = [
    "compressed_segment_from_dict",
    "compressed_segment_to_dict",
    "hot_state_from_dict",
    "hot_state_to_dict",
    "message_ref_from_dict",
    "message_ref_to_dict",
    "message_from_dict",
    "message_to_dict",
    "package_from_dict",
    "package_to_dict",
    "recall_document_from_dict",
    "recall_document_to_dict",
    "tool_call_from_dict",
    "tool_call_to_dict",
]
