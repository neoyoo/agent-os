import pytest

from agentos.attachments import AttachmentRuntime, ImagePart, TextPart
from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.compression import CompressionRuntime
from agentos.context import ContextRenderer, ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderToolCall,
    ProviderResponse,
    UserMessage,
    provider_message_to_dict,
)
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
    assert [
        provider_message_to_dict(message)
        for message in messages.materialize_provider_messages()
    ] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Fake assistant response."},
    ]
    assert [provider_message_to_dict(message) for message in provider.requests[0].messages] == [
        {"role": "user", "content": "Hello"},
    ]
    assert "Run a fake provider loop." in provider.requests[0].system


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
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            attachment_runtime=attachments,
        ),
        provider=provider,
    )

    loop.run_turn("分析图片", attachments=[attachment])
    loop.run_turn("继续")

    first_user = provider.requests[0].messages[0]
    second_user = provider.requests[1].messages[0]
    assert first_user == UserMessage(
        content=(
            ImagePart(attachment),
            TextPart("分析图片"),
        ),
    )
    assert isinstance(second_user.content, str)
    assert "Attachment att_1" in second_user.content


def test_query_loop_recalls_attachment_through_recall_context_namespace() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_recall",
                        name="recall_context",
                        arguments={"handle": f"att:{attachment.handle}"},
                    ),
                ],
            ),
            ProviderResponse(content="inspected"),
        ],
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
        attachment_runtime=attachments,
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
            attachment_runtime=attachments,
        ),
        provider=provider,
        tool_call_router=router,
    )

    result = loop.run_turn("再看一下附件")

    assert result == "inspected"
    assert provider.requests[1].messages[-1] == UserMessage(
        content=(
            ImagePart(attachment),
            TextPart(f"Recalled attachment {attachment.handle} for inspection."),
        ),
    )


def test_query_loop_keeps_loaded_image_visible_through_tool_iterations() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_image",
                        name="load_image",
                        arguments={"handle": f"att:{attachment.handle}"},
                    ),
                ],
            ),
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_state",
                        name="declare_schema",
                        arguments={
                            "fields": [
                                {
                                    "name": "drawing_seen",
                                    "type": "str",
                                    "purpose": "whether image was inspected",
                                },
                            ],
                        },
                    ),
                ],
            ),
            ProviderResponse(content="inspected"),
        ],
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
        attachment_runtime=attachments,
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
            attachment_runtime=attachments,
        ),
        provider=provider,
        tool_call_router=router,
    )

    result = loop.run_turn("load then inspect")

    assert result == "inspected"
    assert provider.requests[1].messages[0] == UserMessage(
        content=(
            TextPart("load then inspect"),
            TextPart(
                "Loaded image diagram.png (handle: att:att_1) for inspection. "
                "Use the attached image content when answering.",
            ),
            ImagePart(attachment),
        ),
    )
    assert provider.requests[2].messages[0] == provider.requests[1].messages[0]
    assert not (
        isinstance(provider.requests[2].messages[-1], UserMessage)
        and isinstance(provider.requests[2].messages[-1].content, tuple)
        and any(
            isinstance(part, ImagePart)
            for part in provider.requests[2].messages[-1].content
        )
    )
    assert all(
        not (
            isinstance(message, UserMessage)
            and isinstance(message.content, tuple)
            and any(isinstance(part, ImagePart) for part in message.content)
        )
        for message in messages.materialize_provider_messages()
    )


def test_query_loop_ignores_duplicate_tool_call_in_same_turn() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    repeated = ProviderToolCall(
        id="call_state",
        name="update_state",
        arguments={"field_name": "drawing_info", "value": "same"},
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_schema",
                        name="declare_schema",
                        arguments={
                            "fields": [
                                {
                                    "name": "drawing_info",
                                    "type": "str",
                                    "purpose": "drawing facts",
                                },
                            ],
                        },
                    ),
                ],
            ),
            ProviderResponse(tool_calls=[repeated]),
            ProviderResponse(tool_calls=[repeated]),
            ProviderResponse(content="done"),
        ],
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
    )

    result = loop.run_turn("analyze")

    assert result == "done"
    assert context.state.working_state["drawing_info"] == "same"
    tool_messages = [
        provider_message_to_dict(message)
        for message in messages.materialize_provider_messages()
        if provider_message_to_dict(message)["role"] == "tool"
    ]
    assert tool_messages[-1]["content"] == (
        "duplicate tool call ignored: update_state with identical arguments "
        "was already applied in this turn; continue with the next step or "
        "return the final answer"
    )


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

    assert [provider_message_to_dict(message) for message in provider.requests[0].messages] == [
        {"role": "user", "content": "First detail"},
    ]
    assert [provider_message_to_dict(message) for message in provider.requests[1].messages] == [
        {"role": "user", "content": "Current task"},
    ]
    assert '<segment id="seg_1"' in provider.requests[1].system

    RecallRuntime(
        compression_index=compression.index,
        message_runtime=messages,
    ).recall_context("seg_1")
    recalled_request = loop.build_request()
    next_request = loop.build_request()

    assert [
        provider_message_to_dict(message)["content"]
        for message in recalled_request.messages
    ] == [
        "First detail",
        "Captured first history.",
        "Current task",
        "Second answer.",
    ]
    assert [
        provider_message_to_dict(message)["content"]
        for message in next_request.messages
    ] == [
        "Current task",
        "Second answer.",
    ]
