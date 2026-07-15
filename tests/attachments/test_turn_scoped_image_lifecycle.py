import asyncio

import pytest

from agentos import Agent
from agentos.attachments import AttachmentRuntime, ImagePart, TextPart
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderToolCall,
    ProviderInputItem,
)
from agentos.runtime import LocalContinuationInput, ProviderRequestBuilder, QueryLoop
from tests._context_protocol_fixtures import default_context_renderer


def build_agent(
    *,
    context: ContextRuntime,
    messages: MessageRuntime,
    attachment_runtime: AttachmentRuntime,
    provider: object,
    router: ToolCallRouter | None = None,
    turn_notice_provider: object | None = None,
) -> Agent:
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs() if router is not None else [],
            attachment_runtime=attachment_runtime,
        ),
        provider=provider,
        tool_call_router=router,
        turn_notice_provider=turn_notice_provider,
    )
    return Agent(query_loop=loop)


def assert_no_loaded_attachments(attachments: AttachmentRuntime) -> None:
    item = ProviderInputItem.business_user("next")
    assert attachments._project_provider_inputs_compat((item,)) == (item,)


def test_load_attachment_projects_to_rest_of_turn_provider_requests() -> None:
    async def run() -> None:
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
                            name="load_attachment",
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
        agent = build_agent(
            context=context,
            messages=messages,
            attachment_runtime=attachments,
            provider=provider,
            router=router,
        )

        result = await agent.run("inspect")

        assert result.content == "done"
        loaded = (
            TextPart(f"Loaded attachment {attachment.handle} for inspection."),
            ImagePart(attachment),
        )
        assert provider.requests[1].messages[-1].content == loaded
        assert provider.requests[2].messages[-1].content == loaded

    asyncio.run(run())


def test_context_tool_and_attachment_load_share_unified_turn() -> None:
    async def run() -> None:
        context = ContextRuntime()
        context.declare_schema(
            [
                WorkingStateField(
                    name="drawing_info",
                    type="object",
                    purpose="Drawing facts",
                ),
            ],
        )
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
                            id="call_update",
                            name="update_state",
                            arguments={
                                "field_name": "drawing_info",
                                "value": {"material": "C45"},
                            },
                        ),
                        ProviderToolCall(
                            id="call_load",
                            name="load_attachment",
                            arguments={"handle": f"att:{attachment.handle}"},
                        ),
                    ],
                ),
                ProviderResponse(content="done"),
            ],
        )
        agent = build_agent(
            context=context,
            messages=messages,
            attachment_runtime=attachments,
            provider=provider,
            router=router,
        )

        result = await agent.run("inspect and update")

        assert result.content == "done"
        assert context.snapshot().working_state == {
            "drawing_info": {"material": "C45"},
        }
        loaded = (
            TextPart(f"Loaded attachment {attachment.handle} for inspection."),
            ImagePart(attachment),
        )
        assert provider.requests[1].messages[-1].content == loaded
        applied = [
            message
            for message in provider.requests[1].messages
            if getattr(message, "tool_call_id", "") == "call_update"
        ]
        assert len(applied) == 1
        assert applied[0].content[0].text == "context tool update_state applied"  # type: ignore[union-attr]

    asyncio.run(run())


def test_next_turn_requires_explicit_load_attachment() -> None:
    async def run() -> None:
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
                            name="load_attachment",
                            arguments={"handle": f"att:{attachment.handle}"},
                        ),
                    ],
                ),
                ProviderResponse(content="first done"),
                ProviderResponse(content="second done"),
            ],
        )
        agent = build_agent(
            context=context,
            messages=messages,
            attachment_runtime=attachments,
            provider=provider,
            router=router,
        )

        await agent.run("inspect")
        await agent.run("continue")

        assert "Loaded attachment" in str(provider.requests[1].messages[-1].content)
        assert all(
            "Loaded attachment" not in str(message.content)
            for message in provider.requests[2].messages
        )

    asyncio.run(run())


def test_continuation_turn_clears_loaded_attachments() -> None:
    class OneNotice:
        def __init__(self) -> None:
            self._used = False

        def consume_notices(self) -> tuple[str, ...]:
            if self._used:
                return ()
            self._used = True
            return ("continue",)

    async def run() -> None:
        context = ContextRuntime()
        messages = MessageRuntime()
        attachments = AttachmentRuntime()
        attachment = attachments.upload_bytes(
            b"image-bytes",
            filename="diagram.png",
            mime_type="image/png",
        )
        attachments.load_attachment_handle(f"att:{attachment.handle}")
        provider = FakeProvider([ProviderResponse(content="continued")])
        agent = build_agent(
            context=context,
            messages=messages,
            attachment_runtime=attachments,
            provider=provider,
            turn_notice_provider=OneNotice(),
        )

        result = await agent.run(LocalContinuationInput())

        assert result.content == "continued"
        assert "Loaded attachment" in str(provider.requests[0].messages[-1].content)
        assert_no_loaded_attachments(attachments)

    asyncio.run(run())


def test_consumer_cancellation_clears_loaded_attachments() -> None:
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

    async def run() -> None:
        context = ContextRuntime()
        messages = MessageRuntime()
        attachments = AttachmentRuntime()
        attachment = attachments.upload_bytes(
            b"image-bytes",
            filename="diagram.png",
            mime_type="image/png",
        )
        attachments.load_attachment_handle(f"att:{attachment.handle}")
        provider = WaitingProvider()
        agent = build_agent(
            context=context,
            messages=messages,
            attachment_runtime=attachments,
            provider=provider,
        )

        stream = await agent.run("inspect", stream=True)

        async def collect() -> None:
            async with stream:
                async for _ in stream:
                    pass

        task = asyncio.create_task(collect())
        await provider.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert_no_loaded_attachments(attachments)

    asyncio.run(run())


def test_consumer_close_clears_loaded_attachments() -> None:
    async def run() -> None:
        context = ContextRuntime()
        messages = MessageRuntime()
        attachments = AttachmentRuntime()
        attachment = attachments.upload_bytes(
            b"image-bytes",
            filename="diagram.png",
            mime_type="image/png",
        )
        attachments.load_attachment_handle(f"att:{attachment.handle}")
        agent = build_agent(
            context=context,
            messages=messages,
            attachment_runtime=attachments,
            provider=FakeProvider([ProviderResponse(content="unused")]),
        )

        stream = await agent.run("inspect", stream=True)
        await anext(stream)
        await stream.aclose()

        assert_no_loaded_attachments(attachments)

    asyncio.run(run())
