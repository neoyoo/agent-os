import pytest

from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime, ToolCall
from agentos.policies import BudgetPolicy
from agentos.recall import RecallContextError, RecallRuntime


def test_recall_context_injects_original_messages_for_one_request() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Original detail")
    message_runtime.append_assistant("Original answer")
    message_runtime.append_user("Current question")
    compression = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    compression.maybe_compress()

    RecallRuntime(
        compression_index=compression.index,
        message_runtime=message_runtime,
    ).recall_context("seg_1")

    first_request = message_runtime.materialize_provider_messages()
    second_request = message_runtime.materialize_provider_messages()

    assert [message["content"] for message in first_request] == [
        "Original detail",
        "Original answer",
        "Current question",
    ]
    assert [message["content"] for message in second_request] == ["Current question"]
    assert message_runtime.store.get(old_user.id).content == "Original detail"


def test_recall_context_can_reinject_segment_after_previous_recall_was_consumed() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    message_runtime.append_user("Original detail")
    message_runtime.append_assistant("Original answer")
    message_runtime.append_user("Current question")
    compression = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    compression.maybe_compress()
    recall = RecallRuntime(
        compression_index=compression.index,
        message_runtime=message_runtime,
    )

    recall.recall_context("seg_1")
    first_request = message_runtime.materialize_provider_messages()
    recall.recall_context("seg_1")
    second_request = message_runtime.materialize_provider_messages()
    third_request = message_runtime.materialize_provider_messages()

    assert [message["content"] for message in first_request] == [
        "Original detail",
        "Original answer",
        "Current question",
    ]
    assert [message["content"] for message in second_request] == [
        "Original detail",
        "Original answer",
        "Current question",
    ]
    assert [message["content"] for message in third_request] == ["Current question"]


def test_recall_context_restores_tool_use_and_tool_result_pair() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    message_runtime.append_user("Read the file")
    message_runtime.append_assistant(
        "Calling tool",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    message_runtime.append_tool_result("call_1", "file content")
    message_runtime.append_user("Continue")
    compression = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=3, retain_latest_messages=2),
    )
    compression.maybe_compress()

    RecallRuntime(
        compression_index=compression.index,
        message_runtime=message_runtime,
    ).recall_context("seg_1")

    recalled_messages = message_runtime.materialize_provider_messages()

    assert recalled_messages[1]["tool_calls"] == [
        {
            "id": "call_1",
            "name": "read_file",
        },
    ]
    assert recalled_messages[2]["role"] == "tool"
    assert recalled_messages[2]["tool_call_id"] == "call_1"


def test_recall_context_raises_for_unknown_handle() -> None:
    runtime = RecallRuntime(
        compression_index=CompressionRuntime(
            context_runtime=ContextRuntime(),
            message_runtime=MessageRuntime(),
            budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        ).index,
        message_runtime=MessageRuntime(),
    )

    with pytest.raises(RecallContextError, match="unknown compressed segment"):
        runtime.recall_context("seg_missing")
