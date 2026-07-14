from __future__ import annotations

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.executor import ToolExecutionResult
from agentos.compression import CompressionIndex, CompressionRuntime
from agentos.context import ContextRuntime
from agentos.events import EventBus, RecallContextInjectedEvent
from agentos.messages import (
    ActiveWindow,
    MessageRef,
    MessageRuntime,
    MessageStore,
    StoredMessage,
    ToolCall,
)
from agentos.policies import BudgetPolicy
from agentos.providers import ProviderToolCall
from agentos.recall import RecallContextError, RecallRuntime

import pytest


class RecordingActiveWindow(ActiveWindow):
    def __init__(self) -> None:
        super().__init__()
        self.prepend_calls: list[tuple[str, ...]] = []

    def prepend_temporary(self, message_ids: tuple[str, ...]) -> None:
        assert isinstance(message_ids, tuple)
        self.prepend_calls.append(message_ids)
        super().prepend_temporary(message_ids)


class InjectedEventRecorder:
    def __init__(self, message_runtime: MessageRuntime) -> None:
        self.message_runtime = message_runtime
        self.active_refs_at_injected_event: tuple[MessageRef, ...] | None = None

    def record(self, event: object) -> None:
        if isinstance(event, RecallContextInjectedEvent):
            self.active_refs_at_injected_event = (
                self.message_runtime.active_window.snapshot_refs()
            )


def _compressed_runtime() -> tuple[
    MessageRuntime,
    CompressionRuntime,
    StoredMessage,
    StoredMessage,
    StoredMessage,
]:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime(active_window=RecordingActiveWindow())
    old_user = message_runtime.append_user("Original detail")
    old_assistant = message_runtime.append_assistant("Original answer")
    current_user = message_runtime.append_user("Current question")
    compression = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    compression.maybe_compress()
    return (
        message_runtime,
        compression,
        old_user,
        old_assistant,
        current_user,
    )


def test_recall_context_atomically_prepends_original_messages_before_event() -> None:
    messages, compression, old_user, old_assistant, current_user = (
        _compressed_runtime()
    )
    recorder = InjectedEventRecorder(messages)
    event_bus = EventBus(subscribers=[recorder])

    recalled = RecallRuntime(
        compression_index=compression.index,
        message_runtime=messages,
        event_bus=event_bus,
    ).recall_context("seg_1")

    assert recalled == (old_user, old_assistant)
    assert isinstance(recalled, tuple)
    assert messages.active_window.prepend_calls == [  # type: ignore[attr-defined]
        (old_user.id, old_assistant.id),
    ]
    expected_refs = (
        MessageRef(old_user.id, temporary=True),
        MessageRef(old_assistant.id, temporary=True),
        MessageRef(current_user.id),
    )
    assert messages.active_window.snapshot_refs() == expected_refs
    assert recorder.active_refs_at_injected_event == expected_refs
    assert messages.store.get(old_user.id).content == "Original detail"


def test_recall_context_deduplicates_repeated_segment_in_window() -> None:
    messages, compression, old_user, old_assistant, current_user = (
        _compressed_runtime()
    )
    recall = RecallRuntime(
        compression_index=compression.index,
        message_runtime=messages,
    )

    first = recall.recall_context("seg_1")
    second = recall.recall_context("seg_1")

    assert first == second == (old_user, old_assistant)
    assert messages.active_window.snapshot_refs() == (
        MessageRef(old_user.id, temporary=True),
        MessageRef(old_assistant.id, temporary=True),
        MessageRef(current_user.id),
    )
    assert messages.active_window.prepend_calls == [  # type: ignore[attr-defined]
        (old_user.id, old_assistant.id),
        (old_user.id, old_assistant.id),
    ]


def test_recall_context_preserves_tool_pair_order_in_temporary_window() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime(active_window=RecordingActiveWindow())
    first = message_runtime.append_user("Read the file")
    assistant = message_runtime.append_assistant(
        "Calling tool",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    result = message_runtime.append_tool_result("call_1", "file content")
    current = message_runtime.append_user("Continue")
    compression = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=3, retain_latest_messages=2),
    )
    compression.maybe_compress()

    recalled = RecallRuntime(
        compression_index=compression.index,
        message_runtime=message_runtime,
    ).recall_context("seg_1")

    assert recalled == (first, assistant, result)
    assert recalled[1].tool_calls == (ToolCall(id="call_1", name="read_file"),)
    assert recalled[2].tool_call_id == "call_1"
    assert message_runtime.active_window.snapshot_refs() == (
        MessageRef(first.id, temporary=True),
        MessageRef(assistant.id, temporary=True),
        MessageRef(result.id, temporary=True),
        MessageRef(current.id),
    )


def test_recall_context_unknown_handle_does_not_modify_window() -> None:
    message_runtime = MessageRuntime()
    current = message_runtime.append_user("Current question")
    before = message_runtime.active_window.snapshot_refs()
    runtime = RecallRuntime(
        compression_index=CompressionIndex(),
        message_runtime=message_runtime,
    )

    with pytest.raises(RecallContextError, match="unknown compressed segment"):
        runtime.recall_context("seg_missing")

    assert before == (MessageRef(current.id),)
    assert message_runtime.active_window.snapshot_refs() == before


def test_recall_context_source_read_failure_does_not_modify_window() -> None:
    store = MessageStore()
    first = StoredMessage("msg_old", "user", "Old evidence")
    store.put(first)
    message_runtime = MessageRuntime(store=store)
    current = message_runtime.append_user("Current question")
    index = CompressionIndex()
    index.record("seg_1", [first.id, "msg_missing"])
    before = message_runtime.active_window.snapshot_refs()

    with pytest.raises(KeyError, match="msg_missing"):
        RecallRuntime(
            compression_index=index,
            message_runtime=message_runtime,
        ).recall_context("seg_1")

    assert before == (MessageRef(current.id),)
    assert message_runtime.active_window.snapshot_refs() == before


def test_recall_router_returns_standard_tool_result_and_injects_originals() -> None:
    messages, compression, old_user, old_assistant, current_user = (
        _compressed_runtime()
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        recall_runtime=RecallRuntime(
            compression_index=compression.index,
            message_runtime=messages,
        ),
    )

    result = router.execute_tool_call(
        ProviderToolCall(
            id="call_recall",
            name="recall_context",
            arguments={"handle": "seg_1"},
        ),
    )

    assert isinstance(result, ToolExecutionResult)
    assert result.tool_call_id == "call_recall"
    assert '<recalled-context source="compressed_history" handle="seg_1">' in (
        result.content
    )
    assert "Original detail" in result.content
    assert messages.active_window.snapshot_refs() == (
        MessageRef(old_user.id, temporary=True),
        MessageRef(old_assistant.id, temporary=True),
        MessageRef(current_user.id),
    )
