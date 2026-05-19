import asyncio

import pytest

from agentos.attachments import AttachmentRuntime, ImagePart, TextPart
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderToolCall,
    UserMessage,
)
from agentos.runtime import AsyncQueryLoop, ProviderRequestBuilder, QueryLoop


def test_load_image_persists_across_provider_requests_in_same_turn() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    tools = ToolRegistry()
    tools.register(
        RegisteredTool(
            name="noop",
            description="No-op.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: "ok",
        ),
    )
    router = ToolCallRouter(
        tool_registry=tools,
        context_runtime=context,
        attachment_runtime=attachments,
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_load",
                        name="load_image",
                        arguments={"handle": f"att:{attachment.handle}"},
                    ),
                ],
            ),
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(id="call_noop", name="noop", arguments={}),
                ],
            ),
            ProviderResponse(content="done"),
        ],
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

    assert loop.run_turn("inspect") == "done"

    loaded = UserMessage(
        content=(
            TextPart(f"Loaded image {attachment.handle} for inspection."),
            ImagePart(attachment),
        ),
    )
    assert provider.requests[1].messages[-1] == loaded
    assert provider.requests[2].messages[-1] == loaded


def test_next_turn_requires_explicit_load_image() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
        attachment_runtime=attachments,
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_load",
                        name="load_image",
                        arguments={"handle": f"att:{attachment.handle}"},
                    ),
                ],
            ),
            ProviderResponse(content="first done"),
            ProviderResponse(content="second done"),
        ],
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

    loop.run_turn("inspect")
    loop.run_turn("continue")

    assert "Loaded image" in str(provider.requests[1].messages[-1].content)
    assert all("Loaded image" not in str(message.content) for message in provider.requests[2].messages)


def test_continuation_turn_also_clears_loaded_images() -> None:
    class OneNotice:
        def __init__(self) -> None:
            self._used = False

        def consume_notices(self) -> tuple[str, ...]:
            if self._used:
                return ()
            self._used = True
            return ("continue",)

    context = ContextRuntime()
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    attachments.load_image_handle(f"att:{attachment.handle}")
    provider = FakeProvider([ProviderResponse(content="continued")])
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            attachment_runtime=attachments,
        ),
        provider=provider,
        turn_notice_provider=OneNotice(),
    )

    list(loop.run_continuation_stream())

    assert "Loaded image" in str(provider.requests[0].messages[-1].content)
    assert attachments.project_provider_messages([UserMessage(content="next")]) == [
        UserMessage(content="next"),
    ]


def test_async_cancel_still_clears_loaded_images() -> None:
    class WaitingProvider:
        timeout_seconds = None

        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def async_stream(
            self,
            request: ProviderRequest,
            options: ProviderStreamOptions,
        ):
            self.started.set()
            yield ProviderStreamStarted(request_id="wait")
            await asyncio.Event().wait()

        def complete(self, request: ProviderRequest) -> ProviderResponse:
            raise AssertionError("async stream should be used")

    async def run() -> AttachmentRuntime:
        context = ContextRuntime()
        messages = MessageRuntime()
        attachments = AttachmentRuntime()
        attachment = attachments.upload_bytes(
            b"image-bytes",
            filename="diagram.png",
            mime_type="image/png",
        )
        attachments.load_image_handle(f"att:{attachment.handle}")
        provider = WaitingProvider()
        loop = AsyncQueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                attachment_runtime=attachments,
            ),
            provider=provider,
        )

        async def collect() -> None:
            async for _ in loop.run_turn_stream("inspect"):
                pass

        task = asyncio.create_task(collect())
        await provider.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return attachments

    attachments = asyncio.run(run())

    assert attachments.project_provider_messages([UserMessage(content="next")]) == [
        UserMessage(content="next"),
    ]
