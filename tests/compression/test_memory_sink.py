import pytest

from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime
from agentos.memory import CompressedSegmentPackage
from agentos.messages import MessageRuntime
from agentos.policies import BudgetPolicy


class RecordingMemorySink:
    def __init__(self, message_runtime: MessageRuntime) -> None:
        self.message_runtime = message_runtime
        self.packages: list[CompressedSegmentPackage] = []
        self.active_ids_at_record: list[str] = []

    def record_compressed_segment(self, package: CompressedSegmentPackage) -> None:
        self.packages.append(package)
        self.active_ids_at_record = [
            message.id for message in self.message_runtime.materialize_active()
        ]


class FailingMemorySink:
    def record_compressed_segment(self, package: CompressedSegmentPackage) -> None:
        raise RuntimeError("memory sink failed")


def test_compression_runtime_records_package_before_removing_active_refs() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Old requirement")
    old_assistant = message_runtime.append_assistant("Old answer")
    current_user = message_runtime.append_user("Current task")
    sink = RecordingMemorySink(message_runtime)
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        memory_sink=sink,
        session_id="session_1",
    )

    segment = runtime.maybe_compress()

    assert segment is not None
    assert len(sink.packages) == 1
    assert sink.packages[0].segment == segment
    assert sink.packages[0].source_refs == (old_user.id, old_assistant.id)
    assert sink.packages[0].recall_document.session_id == "session_1"
    assert sink.active_ids_at_record == [old_user.id, old_assistant.id, current_user.id]
    assert [message.id for message in message_runtime.materialize_active()] == [
        current_user.id,
    ]
    assert runtime.index.source_refs("seg_1") == [old_user.id, old_assistant.id]


def test_compression_runtime_keeps_active_refs_if_memory_sink_fails() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Old requirement")
    old_assistant = message_runtime.append_assistant("Old answer")
    current_user = message_runtime.append_user("Current task")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        memory_sink=FailingMemorySink(),
        session_id="session_1",
    )

    with pytest.raises(RuntimeError, match="memory sink failed"):
        runtime.maybe_compress()

    assert [message.id for message in message_runtime.materialize_active()] == [
        old_user.id,
        old_assistant.id,
        current_user.id,
    ]
    assert context_runtime.state.compressed_history == ()
    assert runtime.index.snapshot() == {}
    assert runtime.next_segment_number() == 1


def test_compression_runtime_preserves_old_behavior_without_memory_sink() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Old requirement")
    old_assistant = message_runtime.append_assistant("Old answer")
    current_user = message_runtime.append_user("Current task")
    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )

    segment = runtime.maybe_compress()

    assert segment is not None
    assert context_runtime.state.compressed_history == (segment,)
    assert runtime.index.source_refs("seg_1") == [old_user.id, old_assistant.id]
    assert [message.id for message in message_runtime.materialize_active()] == [
        current_user.id,
    ]
