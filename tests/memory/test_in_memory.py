import pytest

from agentos.context import CompressedSegment
from agentos.memory import CompressedSegmentPackage, HotSessionState, SegmentRecallDocument
from agentos.memory.in_memory import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
    InMemoryRecallIndex,
)
from agentos.messages import Message, MessageRef
from agentos.runtime import SessionState


def build_package(segment_id: str = "seg_1") -> CompressedSegmentPackage:
    segment = CompressedSegment(
        id=segment_id,
        topic="读取 pyproject.toml 里的项目名",
        summary="工具返回 project.name = agent-os。",
    )
    document = SegmentRecallDocument(
        session_id="session_1",
        segment_id=segment_id,
        topic=segment.topic,
        summary=segment.summary,
        keywords=("pyproject.toml", "agent-os"),
        tool_hints=("read_file(path=pyproject.toml)",),
        searchable_text="python project metadata",
    )
    return CompressedSegmentPackage(
        segment=segment,
        source_refs=("msg_1", "msg_2"),
        recall_document=document,
    )


def test_in_memory_hot_store_saves_hot_messages_refs_and_temporary_refs() -> None:
    store = InMemoryHotSessionStore()
    message = Message(id="msg_1", role="user", content="hello")

    store.save_hot_state(
        HotSessionState(
            session_id="session_1",
            active_refs=[MessageRef("msg_1")],
            recent_messages=[message],
        ),
    )
    store.append_hot_message("session_1", Message(id="msg_2", role="assistant", content="ok"))
    store.save_segment_refs("session_1", "seg_1", ["msg_1", "msg_2"])
    store.set_temporary_recalled_refs("session_1", ["msg_1"])

    assert store.load_hot_state("session_1") is not None
    assert store.get_hot_messages("session_1", ["msg_1", "msg_2"]) == [
        message,
        Message(id="msg_2", role="assistant", content="ok"),
    ]
    assert store.get_segment_refs("session_1", "seg_1") == ("msg_1", "msg_2")
    assert store.consume_temporary_recalled_refs("session_1") == ("msg_1",)
    assert store.consume_temporary_recalled_refs("session_1") == ()


def test_in_memory_hot_store_returns_none_when_any_hot_message_is_missing() -> None:
    store = InMemoryHotSessionStore()
    store.append_hot_message("session_1", Message(id="msg_1", role="user", content="hello"))

    assert store.get_hot_messages("session_1", ["msg_1", "missing"]) is None


def test_in_memory_durable_store_saves_messages_segments_and_active_refs() -> None:
    store = InMemoryDurableSessionStore()
    session = SessionState(id="session_1")
    package = build_package()
    messages = [
        Message(id="msg_1", role="user", content="Read pyproject"),
        Message(id="msg_2", role="assistant", content="agent-os"),
    ]

    store.save_session(session)
    for message in messages:
        store.append_message("session_1", message)
    store.save_active_refs("session_1", [MessageRef("msg_2")])
    store.save_compressed_segment("session_1", package)

    assert store.load_session("session_1") == session
    assert store.get_messages("session_1", ["msg_1", "msg_2"]) == messages
    assert store.load_active_refs("session_1") == (MessageRef("msg_2"),)
    assert store.get_segment_refs("session_1", "seg_1") == ("msg_1", "msg_2")
    assert store.list_compressed_segments("session_1") == (package.segment,)


def test_in_memory_durable_store_raises_for_missing_message() -> None:
    store = InMemoryDurableSessionStore()

    with pytest.raises(KeyError, match="missing"):
        store.get_messages("session_1", ["missing"])


def test_in_memory_recall_index_searches_by_lexical_overlap() -> None:
    index = InMemoryRecallIndex()
    index.index_segment(build_package("seg_1").recall_document)
    index.index_segment(
        SegmentRecallDocument(
            session_id="session_1",
            segment_id="seg_2",
            topic="unrelated",
            summary="其他历史。",
            keywords=("other",),
        ),
    )

    candidates = index.search_segments("session_1", "pyproject project name", limit=2)

    assert [candidate.segment_id for candidate in candidates] == ["seg_1"]
    assert candidates[0].score is not None
    assert candidates[0].score > 0
