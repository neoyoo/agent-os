from agentos.compression import RuleBasedCompressor
from agentos.memory import CompressedSegmentPackage
from agentos.messages import Message, ToolCall


def test_rule_based_compressor_builds_segment_package_for_recall() -> None:
    messages = [
        Message(
            id="msg_1",
            role="user",
            content="读取 pyproject.toml 里的 [project].name。",
        ),
        Message(
            id="msg_2",
            role="assistant",
            content="Calling tool",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="read_file",
                    arguments={"path": "pyproject.toml"},
                ),
            ],
        ),
        Message(
            id="msg_3",
            role="tool",
            content='[project]\nname = "agent-os"\nrequires-python = ">=3.11"',
            tool_call_id="call_1",
        ),
    ]

    package = RuleBasedCompressor().compress_package(
        segment_id="seg_1",
        session_id="session_1",
        messages=messages,
    )

    assert isinstance(package, CompressedSegmentPackage)
    assert package.segment.id == "seg_1"
    assert package.source_refs == ("msg_1", "msg_2", "msg_3")
    assert package.recall_document.session_id == "session_1"
    assert package.recall_document.segment_id == "seg_1"
    assert "pyproject.toml" in package.recall_document.keywords
    assert "agent-os" in package.recall_document.keywords
    assert "read_file(path=pyproject.toml)" in package.recall_document.tool_hints


def test_rule_based_compressor_clips_recall_search_text() -> None:
    long_payload = "x" * 1200

    package = RuleBasedCompressor().compress_package(
        segment_id="seg_1",
        session_id="session_1",
        messages=[
            Message(
                id="msg_1",
                role="tool",
                content=f"large output {long_payload}",
                tool_call_id="call_1",
            ),
        ],
    )

    assert long_payload not in package.recall_document.searchable_text
    assert len(package.recall_document.searchable_text) <= 500
