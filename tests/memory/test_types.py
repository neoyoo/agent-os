from agentos.context import CompressedSegment
from agentos.memory import (
    CompressedSegmentPackage,
    HotSessionState,
    RecallCandidate,
    SegmentRecallDocument,
)
from agentos.messages import Message, MessageRef


def test_segment_recall_document_renders_search_text_without_original_payload() -> None:
    document = SegmentRecallDocument(
        session_id="session_1",
        segment_id="seg_1",
        topic="读取 pyproject.toml 里的项目名",
        summary="用户要求读取项目名，工具返回 project.name = agent-os。",
        keywords=("pyproject.toml", "project.name", "agent-os"),
        tool_hints=("read_file(path=pyproject.toml)",),
        searchable_text="project metadata lookup",
    )

    rendered = document.to_text()

    assert "读取 pyproject.toml 里的项目名" in rendered
    assert "project.name" in rendered
    assert "read_file(path=pyproject.toml)" in rendered
    assert "project metadata lookup" in rendered
    assert "完整 pyproject 原文不应该出现在 recall document" not in rendered


def test_compressed_segment_package_keeps_visible_segment_refs_and_recall_document() -> None:
    segment = CompressedSegment(
        id="seg_1",
        topic="历史上下文",
        summary="压缩了 2 条历史消息。",
    )
    document = SegmentRecallDocument(
        session_id="session_1",
        segment_id="seg_1",
        topic=segment.topic,
        summary=segment.summary,
    )

    package = CompressedSegmentPackage(
        segment=segment,
        source_refs=("msg_1", "msg_2"),
        recall_document=document,
    )

    assert package.segment is segment
    assert package.source_refs == ("msg_1", "msg_2")
    assert package.recall_document.segment_id == "seg_1"


def test_hot_session_state_freezes_collections() -> None:
    message = Message(id="msg_1", role="user", content="hello")
    state = HotSessionState(
        session_id="session_1",
        active_refs=[MessageRef("msg_1")],
        recent_messages=[message],
        temporary_recalled_refs=["msg_2"],
        segment_refs={"seg_1": ["msg_1"]},
    )

    assert state.active_refs == (MessageRef("msg_1"),)
    assert state.recent_messages == (message,)
    assert state.temporary_recalled_refs == ("msg_2",)
    assert state.segment_refs == {"seg_1": ("msg_1",)}


def test_recall_candidate_is_orderable_by_score_in_callers() -> None:
    candidate = RecallCandidate(
        session_id="session_1",
        segment_id="seg_1",
        score=0.75,
        reason="keyword overlap",
    )

    assert candidate.session_id == "session_1"
    assert candidate.segment_id == "seg_1"
    assert candidate.score == 0.75
    assert candidate.reason == "keyword overlap"
