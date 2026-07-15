from agentos.recall import InMemoryRecallIndex, SegmentRecallDocument


def test_in_memory_recall_index_searches_by_lexical_overlap() -> None:
    index = InMemoryRecallIndex()
    index.index_segment(
        SegmentRecallDocument(
            session_id="session_1",
            segment_id="seg_1",
            topic="读取 pyproject.toml 里的项目名",
            summary="工具返回 project.name = agent-os。",
            keywords=("pyproject.toml", "agent-os"),
            searchable_text="python project metadata",
        ),
    )
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
