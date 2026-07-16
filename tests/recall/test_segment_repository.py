import pytest

from agentos.artifacts import ArtifactRef
from agentos.compression import CompressionIndex
from agentos.context import CompressedSegment
from agentos.persistence import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
)
from agentos.recall import (
    CompressedSegmentPackage,
    InMemoryRecallIndex,
    SegmentRecallDocument,
    SegmentRepository,
)
from agentos.recall.segment_repository import SegmentNotFoundError
from agentos.messages import MessageRuntime, StoredMessage, ToolCall


def build_package(
    segment_id: str = "seg_1",
    *,
    session_id: str = "session_1",
    source_refs: tuple[str, ...] = ("msg_1", "msg_2"),
) -> CompressedSegmentPackage:
    segment = CompressedSegment(
        id=segment_id,
        topic="读取 pyproject.toml 里的项目名",
        summary="工具返回 project.name = agent-os。",
    )
    return CompressedSegmentPackage(
        segment=segment,
        source_refs=source_refs,
        recall_document=SegmentRecallDocument(
            session_id=session_id,
            segment_id=segment_id,
            topic=segment.topic,
            summary=segment.summary,
            keywords=("pyproject.toml", "agent-os"),
            searchable_text="python project metadata",
        ),
    )


def build_runtime() -> tuple[
    SegmentRepository,
    InMemoryHotSessionStore,
    InMemoryDurableSessionStore,
    InMemoryRecallIndex,
]:
    hot_store = InMemoryHotSessionStore()
    durable_store = InMemoryDurableSessionStore()
    recall_index = InMemoryRecallIndex()
    return (
        SegmentRepository(
            hot_store=hot_store,
            durable_store=durable_store,
            recall_index=recall_index,
        ),
        hot_store,
        durable_store,
        recall_index,
    )


def test_segment_repository_records_compressed_segment_package() -> None:
    runtime, hot_store, durable_store, recall_index = build_runtime()
    package = build_package()

    runtime.record_compressed_segment(package)

    assert hot_store.get_segment_refs("session_1", "seg_1") == ("msg_1", "msg_2")
    assert durable_store.get_segment_refs("session_1", "seg_1") == ("msg_1", "msg_2")
    assert durable_store.list_compressed_segments("session_1") == (package.segment,)
    assert recall_index.search_segments("session_1", "pyproject", limit=1)[0].segment_id == "seg_1"


def test_runtime_repository_partitions_same_segment_id_by_session() -> None:
    messages = MessageRuntime()
    messages.hydrate_messages(
        [
            StoredMessage("msg_a", "user", "session A"),
            StoredMessage("msg_b", "user", "session B"),
        ],
    )
    repository = SegmentRepository.from_runtime(
        CompressionIndex(),
        messages,
        session_id="session_a",
    )
    repository.record_compressed_segment(
        build_package("seg_shared", session_id="session_a", source_refs=("msg_a",)),
    )
    repository.record_compressed_segment(
        build_package("seg_shared", session_id="session_b", source_refs=("msg_b",)),
    )

    assert [
        message.content
        for message in repository.recall_by_handle("session_a", "seg_shared")
    ] == ["session A"]
    assert [
        message.content
        for message in repository.recall_by_handle("session_b", "seg_shared")
    ] == ["session B"]


def test_runtime_repository_rejects_cross_session_handle_access() -> None:
    messages = MessageRuntime()
    messages.hydrate_messages([StoredMessage("msg_a", "user", "session A")])
    repository = SegmentRepository.from_runtime(
        CompressionIndex(),
        messages,
        session_id="session_a",
    )
    repository.record_compressed_segment(
        build_package("seg_a", session_id="session_a", source_refs=("msg_a",)),
    )

    with pytest.raises(SegmentNotFoundError):
        repository.recall_by_handle("session_b", "seg_a")


def test_recall_by_handle_prefers_hot_messages_when_available() -> None:
    runtime, hot_store, durable_store, _ = build_runtime()
    package = build_package()
    runtime.record_compressed_segment(package)
    hot_store.append_hot_message(
        "session_1",
        StoredMessage(id="msg_1", role="user", content="hot user"),
    )
    hot_store.append_hot_message(
        "session_1",
        StoredMessage(id="msg_2", role="assistant", content="hot assistant"),
    )
    durable_store.append_message(
        "session_1",
        StoredMessage(id="msg_1", role="user", content="durable user"),
    )
    durable_store.append_message(
        "session_1",
        StoredMessage(id="msg_2", role="assistant", content="durable assistant"),
    )

    messages = runtime.recall_by_handle("session_1", "seg_1")

    assert all(type(message) is StoredMessage for message in messages)
    assert [message.content for message in messages] == ["hot user", "hot assistant"]


def test_recall_by_handle_falls_back_to_durable_messages() -> None:
    runtime, _, durable_store, _ = build_runtime()
    package = build_package()
    runtime.record_compressed_segment(package)
    artifact = ArtifactRef(
        artifact_id="art_550e8400-e29b-41d4-a716-446655440000",
        filename="result.json",
        media_type="application/json",
    )
    durable_messages = [
        StoredMessage(
            id="msg_1",
            role="user",
            content="durable user",
            artifact_refs=(artifact,),
        ),
        StoredMessage(
            id="msg_2",
            role="assistant",
            content="durable assistant",
            artifact_refs=(artifact,),
            tool_calls=(
                ToolCall(
                    id="call_1",
                    name="inspect",
                    arguments={
                        "filters": {
                            "tags": ["phase2", "memory"],
                            "metadata": {"source": "artifact"},
                        },
                    },
                ),
            ),
        ),
    ]
    for message in durable_messages:
        durable_store.append_message("session_1", message)

    messages = runtime.recall_by_handle("session_1", "seg_1")

    assert all(type(message) is StoredMessage for message in messages)
    assert messages == durable_messages
    assert messages[0].artifact_refs == (artifact,)
    assert messages[1].tool_calls[0].arguments == {
        "filters": {
            "tags": ["phase2", "memory"],
            "metadata": {"source": "artifact"},
        },
    }
    assert [message.content for message in messages] == [
        "durable user",
        "durable assistant",
    ]


def test_recall_by_query_uses_recall_index_and_deduplicates_messages() -> None:
    runtime, _, durable_store, _ = build_runtime()
    first = build_package("seg_1")
    second = CompressedSegmentPackage(
        segment=CompressedSegment(
            id="seg_2",
            topic="同一个项目名后续确认",
            summary="再次确认 agent-os。",
        ),
        source_refs=("msg_2", "msg_3"),
        recall_document=SegmentRecallDocument(
            session_id="session_1",
            segment_id="seg_2",
            topic="同一个项目名后续确认",
            summary="再次确认 agent-os。",
            keywords=("agent-os",),
            searchable_text="pyproject name confirmation",
        ),
    )
    runtime.record_compressed_segment(first)
    runtime.record_compressed_segment(second)
    for message in [
        StoredMessage(id="msg_1", role="user", content="question"),
        StoredMessage(id="msg_2", role="assistant", content="agent-os"),
        StoredMessage(id="msg_3", role="user", content="confirm"),
    ]:
        durable_store.append_message("session_1", message)

    messages = runtime.recall_by_query("session_1", "pyproject agent-os", limit=2)

    assert all(type(message) is StoredMessage for message in messages)
    assert [message.id for message in messages] == ["msg_1", "msg_2", "msg_3"]
