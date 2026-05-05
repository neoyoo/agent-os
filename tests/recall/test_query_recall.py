import pytest

from agentos.compression import CompressionIndex
from agentos.context import CompressedSegment
from agentos.memory import CompressedSegmentPackage, MemoryRuntime, SegmentRecallDocument
from agentos.memory.in_memory import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
    InMemoryRecallIndex,
)
from agentos.messages import Message, MessageRuntime
from agentos.recall import RecallContextError, RecallRuntime


def build_memory_runtime() -> tuple[MemoryRuntime, InMemoryDurableSessionStore]:
    durable_store = InMemoryDurableSessionStore()
    runtime = MemoryRuntime(
        hot_store=InMemoryHotSessionStore(),
        durable_store=durable_store,
        recall_index=InMemoryRecallIndex(),
    )
    package = CompressedSegmentPackage(
        segment=CompressedSegment(
            id="seg_1",
            topic="读取 pyproject.toml 里的项目名",
            summary="工具返回 project.name = agent-os。",
        ),
        source_refs=("msg_1", "msg_2"),
        recall_document=SegmentRecallDocument(
            session_id="session_1",
            segment_id="seg_1",
            topic="读取 pyproject.toml 里的项目名",
            summary="工具返回 project.name = agent-os。",
            keywords=("pyproject.toml", "agent-os"),
        ),
    )
    runtime.record_compressed_segment(package)
    durable_store.append_message(
        "session_1",
        Message(id="msg_1", role="user", content="读取 pyproject.toml"),
    )
    durable_store.append_message(
        "session_1",
        Message(id="msg_2", role="assistant", content="项目名是 agent-os"),
    )
    return runtime, durable_store


def test_recall_context_query_hydrates_messages_and_injects_once() -> None:
    memory_runtime, _ = build_memory_runtime()
    messages = MessageRuntime()
    recall = RecallRuntime(
        compression_index=CompressionIndex(),
        message_runtime=messages,
        memory_runtime=memory_runtime,
        session_id="session_1",
    )

    recalled = recall.recall_context(query="pyproject 项目名", limit=1)

    assert [message.id for message in recalled] == ["msg_1", "msg_2"]
    first_request = messages.materialize_provider_messages()
    second_request = messages.materialize_provider_messages()
    assert [message["content"] for message in first_request] == [
        "读取 pyproject.toml",
        "项目名是 agent-os",
    ]
    assert second_request == []


def test_recall_context_rejects_handle_and_query_together() -> None:
    memory_runtime, _ = build_memory_runtime()
    recall = RecallRuntime(
        compression_index=CompressionIndex(),
        message_runtime=MessageRuntime(),
        memory_runtime=memory_runtime,
        session_id="session_1",
    )

    with pytest.raises(RecallContextError, match="either handle or query"):
        recall.recall_context("seg_1", query="pyproject")


def test_recall_context_query_requires_memory_runtime() -> None:
    recall = RecallRuntime(
        compression_index=CompressionIndex(),
        message_runtime=MessageRuntime(),
        session_id="session_1",
    )

    with pytest.raises(RecallContextError, match="memory runtime is required"):
        recall.recall_context(query="pyproject")
