import inspect
from typing import get_type_hints

from agentos.attachments import AttachmentRuntime, ImagePart, TextPart
from agentos.builder import AgentBuilder
from agentos.context import (
    ContextRenderer,
    ContextRuntime,
    ContextSnapshotRenderer,
    WorkingStateField,
)
from agentos.context.models import SystemEnvelope
from agentos.context.projection import (
    default_system_section_registry,
    project_context_state,
)
from agentos.messages import MessageRuntime
from agentos.providers import (
    ProviderFunctionSpec,
    ProviderToolSpec,
)
from agentos.runtime import ProviderRequestBuilder
from agentos.tokens import HeuristicTokenCounter


def _default_renderer() -> ContextRenderer:
    return ContextRenderer(
        registry=default_system_section_registry(),
        token_counter=HeuristicTokenCounter(),
    )


class RecordingRenderer:
    def __init__(self) -> None:
        self.calls = 0

    def render(self) -> SystemEnvelope:
        self.calls += 1
        return SystemEnvelope(text="# Runtime Contract\n\nTrusted only.\n")


class DeferredContextRuntime:
    def snapshot(self) -> None:
        raise AssertionError("Phase 1 must not read dynamic context state")


class EmptyProjectionProvider:
    def projections(self):  # type: ignore[no-untyped-def]
        return ()


class RuntimeProjectionProvider:
    def __init__(self, runtime: ContextRuntime) -> None:
        self.runtime = runtime

    def projections(self):  # type: ignore[no-untyped-def]
        return project_context_state(self.runtime.snapshot())


def _configured_builder(
    *,
    renderer: object,
    messages: MessageRuntime,
    context: ContextRuntime | None = None,
    tools: list[ProviderToolSpec] | None = None,
    attachments: AttachmentRuntime | None = None,
) -> ProviderRequestBuilder:
    return ProviderRequestBuilder(
        context_renderer=renderer,  # type: ignore[arg-type]
        message_runtime=messages,
        tools=[] if tools is None else tools,
        attachment_runtime=attachments,
        snapshot_renderer=ContextSnapshotRenderer(HeuristicTokenCounter()),
        context_projections=(
            EmptyProjectionProvider()
            if context is None
            else RuntimeProjectionProvider(context)
        ),
    )


def test_renderer_injection_boundaries_share_protocol() -> None:
    from agentos.runtime.provider_request_builder import SystemEnvelopeRenderer

    assert get_type_hints(ProviderRequestBuilder)["context_renderer"] is (
        SystemEnvelopeRenderer
    )
    assert get_type_hints(AgentBuilder.context_renderer)["renderer"] is (
        SystemEnvelopeRenderer
    )
    assert tuple(inspect.signature(ProviderRequestBuilder.build).parameters) == (
        "self",
    )


def test_provider_request_builder_calls_renderer_without_dynamic_state() -> None:
    renderer = RecordingRenderer()
    messages = MessageRuntime()
    messages.append_user("Please build it.")

    request = _configured_builder(
        renderer=renderer,
        messages=messages,
    ).build().request

    assert renderer.calls == 1
    assert request.system == "# Runtime Contract\n\nTrusted only.\n"
    assert [message.kind for message in request.messages] == [
        "context_snapshot",
        "business_message",
    ]
    assert request.messages[1].content[0].text == "Please build it."  # type: ignore[union-attr]


def test_provider_request_system_contains_no_working_state() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="string",
                purpose="Current task goal and completion criteria.",
            ),
        ],
    )
    context.update_state("task_goal", "secret-dynamic-value")

    request = _configured_builder(
        renderer=_default_renderer(),
        messages=MessageRuntime(),
        context=context,
    ).build().request

    assert isinstance(request.system, str)
    assert "# Runtime Contract" in request.system
    assert "secret-dynamic-value" not in request.system


def test_provider_request_builder_provides_tool_schema_only_through_tools() -> None:
    tool_schema = ProviderToolSpec(
        function=ProviderFunctionSpec(
            name="dangerous_schema_marker",
            description="Dangerous marker.",
            parameters={
                "type": "object",
                "properties": {"secret": {"type": "string"}},
            },
        ),
    )

    request = _configured_builder(
        renderer=_default_renderer(),
        messages=MessageRuntime(),
        tools=[tool_schema],
    ).build().request

    assert request.tools == (
        ProviderToolSpec(
            function=ProviderFunctionSpec(
                name="dangerous_schema_marker",
                description="Dangerous marker.",
                parameters={
                    "type": "object",
                    "properties": {"secret": {"type": "string"}},
                },
            ),
        ),
    )
    assert "dangerous_schema_marker" not in request.system
    assert "secret" not in request.system


def test_provider_request_builder_preserves_existing_attachment_projection() -> None:
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    content = attachments.prepare_user_message("Analyze the image", [attachment])
    messages.append_user(content)
    builder = _configured_builder(
        renderer=_default_renderer(),
        messages=messages,
        attachments=attachments,
    )

    first_request = builder.build().request
    second_request = builder.build().request

    assert first_request.messages[1].content == (
        TextPart("Analyze the image"),
        ImagePart(attachment),
    )
    assert second_request.messages[-1].content == (
        TextPart(f"Loaded attachment {attachment.handle} for inspection."),
        ImagePart(attachment),
    )
