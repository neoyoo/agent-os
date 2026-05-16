import pytest

from agentos.compression import (
    FallbackCompressor,
    LlmCompressor,
    RuleBasedCompressor,
)
from agentos.context import CompressedSegment
from agentos.messages import Message, ToolCall
from agentos.providers import FakeProvider, provider_message_to_dict


def test_llm_compressor_uses_provider_to_build_segment() -> None:
    provider = FakeProvider(
        [
            "TOPIC: provider request typing\n"
            "SUMMARY: Added frozen provider message dataclasses and adapters.",
        ],
    )
    compressor = LlmCompressor(provider=provider)

    segment = compressor.compress(
        "seg_1",
        [
            Message(id="msg_1", role="user", content="Please type provider messages."),
            Message(id="msg_2", role="assistant", content="Implemented it."),
        ],
    )

    assert segment.id == "seg_1"
    assert segment.topic == "provider request typing"
    assert segment.summary == (
        "Added frozen provider message dataclasses and adapters."
    )
    assert provider.requests[0].system.startswith("你是一个上下文压缩助手")
    assert provider_message_to_dict(provider.requests[0].messages[0]) == {
        "role": "user",
        "content": (
            "user: Please type provider messages.\n"
            "assistant: Implemented it."
        ),
    }


def test_llm_compressor_falls_back_when_output_format_is_loose() -> None:
    provider = FakeProvider(["A loose but still useful summary."])

    segment = LlmCompressor(provider=provider).compress(
        "seg_1",
        [Message(id="msg_1", role="user", content="Summarize this long task.")],
    )

    assert segment.topic == "A loose but still useful summary."
    assert segment.summary == "A loose but still useful summary."


def test_llm_compressor_builds_recall_package() -> None:
    provider = FakeProvider(
        [
            "TOPIC: compression\n"
            "SUMMARY: LLM compressor preserves source refs for recall.",
        ],
    )
    messages = [
        Message(id="msg_1", role="user", content="Compress these messages."),
        Message(id="msg_2", role="assistant", content="Done."),
    ]

    package = LlmCompressor(provider=provider).compress_package(
        segment_id="seg_1",
        session_id="session_1",
        messages=messages,
    )

    assert package.segment.topic == "compression"
    assert package.source_refs == ("msg_1", "msg_2")
    assert package.recall_document.session_id == "session_1"
    assert package.recall_document.segment_id == "seg_1"
    assert "source refs" in package.recall_document.searchable_text


def test_llm_compressor_rejects_empty_message_sequence() -> None:
    with pytest.raises(ValueError, match="empty message sequence"):
        LlmCompressor(provider=FakeProvider(["unused"])).compress("seg_1", [])


def test_llm_compressor_adds_token_budget_instruction() -> None:
    provider = FakeProvider(["TOPIC: budget\nSUMMARY: short."])
    compressor = LlmCompressor(
        provider=provider,
        max_output_tokens=1000,
        compression_ratio=0.25,
    )

    compressor.compress(
        "seg_1",
        [Message(id="msg_1", role="user", content="x" * 800)],
    )

    assert "目标输出上限: 50 tokens" in provider.requests[0].system


def test_fallback_compressor_uses_secondary_when_primary_fails() -> None:
    class FailingCompressor:
        def compress(self, segment_id: str, messages: list[Message]):
            raise RuntimeError("provider unavailable")

    compressor = FallbackCompressor(
        primary=FailingCompressor(),
        fallback=RuleBasedCompressor(),
    )

    segment = compressor.compress(
        "seg_1",
        [Message(id="msg_1", role="user", content="Fallback topic.")],
    )

    assert segment.id == "seg_1"
    assert segment.topic == "Fallback topic."


def test_fallback_compressor_package_preserves_recall_fields_with_basic_fallback() -> None:
    class FailingPackageCompressor:
        def compress(self, segment_id: str, messages: list[Message]):
            raise RuntimeError("provider unavailable")

        def compress_package(
            self,
            segment_id: str,
            session_id: str,
            messages: list[Message],
        ):
            raise RuntimeError("provider unavailable")

    class BasicCompressor:
        def compress(
            self,
            segment_id: str,
            messages: list[Message],
        ) -> CompressedSegment:
            return CompressedSegment(
                id=segment_id,
                topic="Fallback path topic",
                summary="Fallback summary.",
            )

    package = FallbackCompressor(
        primary=FailingPackageCompressor(),
        fallback=BasicCompressor(),  # type: ignore[arg-type]
    ).compress_package(
        segment_id="seg_1",
        session_id="session_1",
        messages=[
            Message(
                id="msg_1",
                role="assistant",
                content="Read src/agentos/runtime/query_loop.py",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments={"path": "src/agentos/runtime/query_loop.py"},
                    ),
                ],
            ),
        ],
    )

    assert "src/agentos/runtime/query_loop.py" in package.recall_document.keywords
    assert (
        "read_file(path=src/agentos/runtime/query_loop.py)"
        in package.recall_document.tool_hints
    )
    assert "Fallback path topic" in package.recall_document.searchable_text
