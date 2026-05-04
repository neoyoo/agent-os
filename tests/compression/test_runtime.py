from typing import get_type_hints

import pytest

from agentos.compression import CompressionRuntime, Compressor
from agentos.compression.runtime import CompressionContextBoundary
from agentos.context import CompressedSegment, ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime, ToolCall
from agentos.policies import BudgetPolicy


class RecordingCompressionContext:
    """测试用 compression context boundary，只记录压缩片段。"""

    def __init__(self) -> None:
        self.compressed_history: list[CompressedSegment] = []

    def append_compressed_segment(self, segment: CompressedSegment) -> None:
        """记录 compression runtime 追加的片段。"""

        self.compressed_history.append(segment)


def test_compressor_protocol_is_publicly_exported() -> None:
    assert Compressor.__name__ == "Compressor"


def test_compression_runtime_uses_context_boundary_protocol() -> None:
    hints = get_type_hints(CompressionRuntime)

    assert hints["context_runtime"] is CompressionContextBoundary


def test_compression_runtime_moves_old_refs_to_compressed_history() -> None:
    context_runtime = RecordingCompressionContext()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Old requirement")
    old_assistant = message_runtime.append_assistant("Old analysis")
    current_user = message_runtime.append_user("Current task")
    current_assistant = message_runtime.append_assistant("Current answer")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=3, retain_latest_messages=2),
    )

    segment = runtime.maybe_compress()

    assert segment is not None
    assert segment.id == "seg_1"
    assert [message.id for message in message_runtime.materialize_active()] == [
        current_user.id,
        current_assistant.id,
    ]
    assert message_runtime.store.get(old_user.id).content == "Old requirement"
    assert message_runtime.store.get(old_assistant.id).content == "Old analysis"
    assert context_runtime.compressed_history == [segment]
    assert runtime.index.source_refs("seg_1") == [old_user.id, old_assistant.id]


def test_compression_runtime_keeps_default_prompt_free_of_runtime_metadata() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    message_runtime.append_user("Old detail")
    message_runtime.append_assistant("Old answer")
    message_runtime.append_user("Current detail")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )

    runtime.maybe_compress()
    rendered = ContextRenderer().render(context_runtime.state)

    assert '<segment id="seg_1"' in rendered
    for forbidden_term in ["source", "message_id", "compression_id"]:
        assert forbidden_term not in rendered


def test_compression_runtime_keeps_active_refs_if_context_append_fails() -> None:
    class FailingContextRuntime(ContextRuntime):
        def append_compressed_segment(self, segment: CompressedSegment) -> None:
            raise RuntimeError("append failed")

    context_runtime = FailingContextRuntime()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Old detail")
    old_assistant = message_runtime.append_assistant("Old answer")
    current_user = message_runtime.append_user("Current detail")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )

    with pytest.raises(RuntimeError, match="append failed"):
        runtime.maybe_compress()

    assert runtime.next_segment_number() == 1
    assert [message.id for message in message_runtime.materialize_active()] == [
        old_user.id,
        old_assistant.id,
        current_user.id,
    ]


def test_compression_runtime_skips_when_active_window_is_within_budget() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    message_runtime.append_user("One")
    message_runtime.append_assistant("Two")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )

    assert runtime.maybe_compress() is None
    assert context_runtime.state.compressed_history == ()
    assert [message.content for message in message_runtime.materialize_active()] == [
        "One",
        "Two",
    ]


def test_evictor_does_not_cut_tool_use_tool_result_pairs() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    first = message_runtime.append_user("Need file")
    assistant = message_runtime.append_assistant(
        "Calling tool",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    result = message_runtime.append_tool_result("call_1", "file content")
    current_user = message_runtime.append_user("Continue")
    current_assistant = message_runtime.append_assistant("Done")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=4, retain_latest_messages=3),
    )

    segment = runtime.maybe_compress()

    assert segment is not None
    assert runtime.index.source_refs(segment.id) == [first.id, assistant.id, result.id]
    assert [message.id for message in message_runtime.materialize_active()] == [
        current_user.id,
        current_assistant.id,
    ]


def test_evictor_skips_compression_when_tool_pair_expansion_would_clear_window() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    user = message_runtime.append_user("Need file")
    assistant = message_runtime.append_assistant(
        "Calling tool",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    result = message_runtime.append_tool_result("call_1", "file content")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )

    assert runtime.maybe_compress() is None
    assert context_runtime.state.compressed_history == ()
    assert [message.id for message in message_runtime.materialize_active()] == [
        user.id,
        assistant.id,
        result.id,
    ]


def test_budget_policy_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="max_active_messages"):
        BudgetPolicy(max_active_messages=0)

    with pytest.raises(ValueError, match="retain_latest_messages"):
        BudgetPolicy(max_active_messages=3, retain_latest_messages=0)
