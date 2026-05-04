import pytest

from agentos.compression import CompressionRuntime
from agentos.context import ContextRenderer, ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.policies import BudgetPolicy
from agentos.recall import RecallRuntime
from agentos.runtime import QueryLoop, ProviderRequestBuilder


def test_query_loop_runs_one_user_to_assistant_turn() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Run a fake provider loop.")
    messages = MessageRuntime()
    provider = FakeProvider(["Fake assistant response."])
    request_builder = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[],
    )

    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=request_builder,
        provider=provider,
    )

    response = loop.run_turn("Hello")

    assert response == "Fake assistant response."
    assert messages.materialize_provider_messages() == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Fake assistant response."},
    ]
    assert provider.requests[0].messages == [{"role": "user", "content": "Hello"}]
    assert "Run a fake provider loop." in provider.requests[0].system


def test_query_loop_rejects_truncated_provider_final_response() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    provider = FakeProvider(
        [
            ProviderResponse(
                content="partial answer",
                stop_reason="length",
            ),
        ],
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=[],
        ),
        provider=provider,
    )

    with pytest.raises(RuntimeError, match="truncated"):
        loop.run_turn("Hello")


def test_query_loop_runs_compression_and_recall_through_provider_requests() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Verify Phase 2 loop behavior.")
    messages = MessageRuntime()
    provider = FakeProvider(
        [
            "Captured first history.",
            "Second answer.",
        ],
    )
    request_builder = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[],
    )
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=request_builder,
        provider=provider,
        compression_runtime=compression,
    )

    loop.run_turn("First detail")
    loop.run_turn("Current task")

    assert provider.requests[0].messages == [
        {"role": "user", "content": "First detail"},
    ]
    assert provider.requests[1].messages == [
        {"role": "user", "content": "Current task"},
    ]
    assert '<segment id="seg_1"' in provider.requests[1].system

    RecallRuntime(
        compression_index=compression.index,
        message_runtime=messages,
    ).recall_context("seg_1")
    recalled_request = loop.build_request()
    next_request = loop.build_request()

    assert [message["content"] for message in recalled_request.messages] == [
        "First detail",
        "Captured first history.",
        "Current task",
        "Second answer.",
    ]
    assert [message["content"] for message in next_request.messages] == [
        "Current task",
        "Second answer.",
    ]
