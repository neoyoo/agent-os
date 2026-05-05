from agentos.context import CompressedSegment
from agentos.memory import CompressedSegmentPackage, SegmentRecallDocument
from agentos.memory.in_memory import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
    InMemoryRecallIndex,
)
from agentos.memory.runtime import MemoryRuntime
from agentos.messages import Message


def build_package(segment_id: str = "seg_1") -> CompressedSegmentPackage:
    segment = CompressedSegment(
        id=segment_id,
        topic="读取 pyproject.toml 里的项目名",
        summary="工具返回 project.name = agent-os。",
    )
    return CompressedSegmentPackage(
        segment=segment,
        source_refs=("msg_1", "msg_2"),
        recall_document=SegmentRecallDocument(
            session_id="session_1",
            segment_id=segment_id,
            topic=segment.topic,
            summary=segment.summary,
            keywords=("pyproject.toml", "agent-os"),
            searchable_text="python project metadata",
        ),
    )


def build_runtime() -> tuple[
    MemoryRuntime,
    InMemoryHotSessionStore,
    InMemoryDurableSessionStore,
    InMemoryRecallIndex,
]:
    hot_store = InMemoryHotSessionStore()
    durable_store = InMemoryDurableSessionStore()
    recall_index = InMemoryRecallIndex()
    return (
        MemoryRuntime(
            hot_store=hot_store,
            durable_store=durable_store,
            recall_index=recall_index,
        ),
        hot_store,
        durable_store,
        recall_index,
    )


def test_memory_runtime_records_compressed_segment_package() -> None:
    runtime, hot_store, durable_store, recall_index = build_runtime()
    package = build_package()

    runtime.record_compressed_segment(package)

    assert hot_store.get_segment_refs("session_1", "seg_1") == ("msg_1", "msg_2")
    assert durable_store.get_segment_refs("session_1", "seg_1") == ("msg_1", "msg_2")
    assert durable_store.list_compressed_segments("session_1") == (package.segment,)
    assert recall_index.search_segments("session_1", "pyproject", limit=1)[0].segment_id == "seg_1"


def test_recall_by_handle_prefers_hot_messages_when_available() -> None:
    runtime, hot_store, durable_store, _ = build_runtime()
    package = build_package()
    runtime.record_compressed_segment(package)
    hot_store.append_hot_message("session_1", Message(id="msg_1", role="user", content="hot user"))
    hot_store.append_hot_message(
        "session_1",
        Message(id="msg_2", role="assistant", content="hot assistant"),
    )
    durable_store.append_message(
        "session_1",
        Message(id="msg_1", role="user", content="durable user"),
    )
    durable_store.append_message(
        "session_1",
        Message(id="msg_2", role="assistant", content="durable assistant"),
    )

    messages = runtime.recall_by_handle("session_1", "seg_1")

    assert [message.content for message in messages] == ["hot user", "hot assistant"]


def test_recall_by_handle_falls_back_to_durable_messages() -> None:
    runtime, _, durable_store, _ = build_runtime()
    package = build_package()
    runtime.record_compressed_segment(package)
    durable_store.append_message(
        "session_1",
        Message(id="msg_1", role="user", content="durable user"),
    )
    durable_store.append_message(
        "session_1",
        Message(id="msg_2", role="assistant", content="durable assistant"),
    )

    messages = runtime.recall_by_handle("session_1", "seg_1")

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
        Message(id="msg_1", role="user", content="question"),
        Message(id="msg_2", role="assistant", content="agent-os"),
        Message(id="msg_3", role="user", content="confirm"),
    ]:
        durable_store.append_message("session_1", message)

    messages = runtime.recall_by_query("session_1", "pyproject agent-os", limit=2)

    assert [message.id for message in messages] == ["msg_1", "msg_2", "msg_3"]
