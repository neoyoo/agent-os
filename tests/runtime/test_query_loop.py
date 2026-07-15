import asyncio

import pytest

from agentos.attachments import AttachmentRuntime, ImagePart, TextPart
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderToolCall,
    ProviderResponse,
)
from agentos.policies import BudgetPolicy
from agentos.recall import RecallRuntime
from agentos.runtime import (
    Agent,
    AgentResult,
    ProviderRequestBuilder,
    QueryLoop,
    UserTurnInput,
)
from tests._provider_binary import payload_from_attachment
from tests._context_protocol_fixtures import default_context_renderer


def _run(loop: QueryLoop, input: str | UserTurnInput) -> str:
    async def run() -> str:
        outcome = await Agent(loop).run(input)
        assert isinstance(outcome, AgentResult)
        return outcome.content

    return asyncio.run(run())


def test_query_loop_runs_one_user_to_assistant_turn() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="string",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Run a fake provider loop.")
    messages = MessageRuntime()
    provider = FakeProvider(["Fake assistant response."])
    request_builder = ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        tools=[],
    )

    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=request_builder,
        provider=provider,
    )

    response = _run(loop, "Hello")

    assert response == "Fake assistant response."
    assert [
        (message.role, message.content) for message in messages.materialize_active()
    ] == [
        ("user", "Hello"),
        ("assistant", "Fake assistant response."),
    ]
    assert [message.kind for message in provider.requests[0].messages] == [
        "context_snapshot",
        "business_message",
    ]
    assert provider.requests[0].messages[1].content[0].text == "Hello"  # type: ignore[union-attr]
    assert context.snapshot().working_state["task_goal"] == "Run a fake provider loop."
    assert "Run a fake provider loop." not in provider.requests[0].system


def test_query_loop_runs_turn_with_one_shot_attachment_expansion() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    provider = FakeProvider(["first", "second"])
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            attachment_runtime=attachments,
        ),
        provider=provider,
    )

    _run(loop, UserTurnInput("分析图片", attachments=(attachment,)))
    _run(loop, "继续")

    first_user = provider.requests[0].messages[1]
    second_user = provider.requests[1].messages[1]
    assert first_user.content == (
        TextPart("分析图片"),
        ImagePart(payload_from_attachment(attachment)),
    )
    assert "Attachment att_1" in second_user.content[0].text  # type: ignore[union-attr]


def test_uploaded_attachment_stays_available_after_first_tool_iteration() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="noop",
            description="No-op.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: "ok",
        ),
    )
    router = ToolCallRouter(
        tool_registry=registry,
        context_runtime=context,
        attachment_runtime=attachments,
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(id="call_noop", name="noop", arguments={}),
                ],
            ),
            ProviderResponse(content="inspected"),
        ],
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
            attachment_runtime=attachments,
        ),
        provider=provider,
        tool_call_router=router,
    )

    result = _run(loop, UserTurnInput("分析图片", attachments=(attachment,)))

    assert result == "inspected"
    assert provider.requests[0].messages[1].content == (
        TextPart("分析图片"),
        ImagePart(payload_from_attachment(attachment)),
    )
    assert provider.requests[1].messages[-1].content == (
        TextPart(f"Loaded attachment {attachment.handle} for inspection."),
        ImagePart(payload_from_attachment(attachment)),
    )


def test_distinct_tool_arguments_still_execute_in_same_turn() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    calls: list[dict[str, object]] = []
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="record_value",
            description="Record a value.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: calls.append(arguments) or "recorded",
        ),
    )
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_1",
                        name="record_value",
                        arguments={"value": "first"},
                    ),
                    ProviderToolCall(
                        id="call_2",
                        name="record_value",
                        arguments={"value": "second"},
                    ),
                ],
            ),
            ProviderResponse(content="done"),
        ],
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
    )

    result = _run(loop, "record")

    assert result == "done"
    assert calls == [{"value": "first"}, {"value": "second"}]


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
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=[],
        ),
        provider=provider,
    )

    with pytest.raises(RuntimeError, match="truncated"):
        _run(loop, "Hello")


def test_query_loop_runs_compression_and_recall_through_provider_requests() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="string",
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
        context_renderer=default_context_renderer(),
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

    _run(loop, "First detail")
    _run(loop, "Current task")

    assert provider.requests[0].messages[1].content[0].text == "First detail"  # type: ignore[union-attr]
    assert provider.requests[1].messages[1].content[0].text == "Current task"  # type: ignore[union-attr]
    assert [
        segment.id for segment in context.snapshot().compressed_history
    ] == ["seg_1"]
    assert "seg_1" not in provider.requests[1].system

    RecallRuntime(
        compression_index=compression.index,
        message_runtime=messages,
    ).recall_context("seg_1")
    recalled_request = request_builder.build().request
    next_request = request_builder.build().request

    assert [item.kind for item in recalled_request.messages][0] == "context_snapshot"
    assert [item.kind for item in next_request.messages][0] == "context_snapshot"
    assert [item.kind for item in recalled_request.messages[1:]] == [
        "recalled_message",
        "recalled_message",
        "business_message",
        "business_message",
    ]
    assert [
        item.content[0].text for item in recalled_request.messages[1:]  # type: ignore[union-attr]
    ] == [
        "First detail",
        "Captured first history.",
        "Current task",
        "Second answer.",
    ]
    assert [item.kind for item in next_request.messages[1:]] == [
        "recalled_message",
        "recalled_message",
        "business_message",
        "business_message",
    ]
    assert [
        item.content[0].text for item in next_request.messages[1:]  # type: ignore[union-attr]
    ] == [
        "First detail",
        "Captured first history.",
        "Current task",
        "Second answer.",
    ]

