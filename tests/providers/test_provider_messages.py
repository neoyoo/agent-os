import importlib.util
from dataclasses import FrozenInstanceError

import pytest

from agentos.attachments import Attachment, BytesSource, ImagePart, TextPart
from agentos.context import ContextRuntime, ContextSnapshotRenderer
from agentos.context.projection import project_context_state
from agentos.messages import MessageRuntime, ToolCall
from agentos.providers import (
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
    provider_tool_spec_from_dict,
    provider_tool_spec_to_dict,
)
from agentos.providers.input_serialization import provider_input_to_dict
from agentos.runtime import ProviderRequestBuilder
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer


def test_provider_input_items_are_frozen_slotted_dataclasses() -> None:
    item = ProviderInputItem.business_user("hello")

    with pytest.raises(FrozenInstanceError):
        item.content = (TextPart("mutated"),)  # type: ignore[misc]

    assert not hasattr(item, "__dict__")


def test_legacy_provider_message_module_is_removed() -> None:
    assert importlib.util.find_spec("agentos.providers.messages") is None


def test_provider_input_serialization_redacts_attachment_content_parts() -> None:
    attachment = Attachment(
        handle="att_1",
        filename="diagram.png",
        mime_type="image/png",
        size_bytes=11,
        source=BytesSource(b"image-bytes"),
    )

    result = provider_input_to_dict(
        ProviderInputItem.context_mount(
            (
                TextPart("分析图片"),
                ImagePart(attachment),
            ),
        ),
    )

    assert result == {
        "role": "user",
        "content": [
            {"type": "text", "text": "分析图片"},
            {
                "type": "image",
                "attachment": {
                    "handle": "att_1",
                    "filename": "diagram.png",
                    "mime_type": "image/png",
                    "size_bytes": 11,
                },
                "detail": "auto",
            },
        ],
    }
    assert "image-bytes" not in str(result)


def test_provider_tool_call_deepcopies_arguments() -> None:
    arguments = {"nested": {"path": "README.md"}}

    tool_call = ProviderToolCall(
        id="call_1",
        name="read_file",
        arguments=arguments,
    )
    arguments["nested"]["path"] = "pyproject.toml"  # type: ignore[index]

    assert tool_call.arguments == {"nested": {"path": "README.md"}}


def test_provider_response_tool_calls_normalized_to_tuple() -> None:
    tool_calls = [
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "README.md"},
        ),
    ]

    response = ProviderResponse(tool_calls=tool_calls)
    tool_calls.append(ProviderToolCall(id="call_2", name="other", arguments={}))

    assert response.tool_calls == (
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "README.md"},
        ),
    )


def test_provider_tool_spec_preserves_canonical_function_schema() -> None:
    spec = ProviderToolSpec(
        function=ProviderFunctionSpec(
            name="read_file",
            description="Read a file.",
            parameters={"type": "object"},
        ),
    )

    as_dict = provider_tool_spec_to_dict(spec)
    restored = provider_tool_spec_from_dict(as_dict)

    assert as_dict == {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object"},
        },
    }
    assert restored == spec


def test_provider_request_normalizes_typed_sequences_to_tuples() -> None:
    request = ProviderRequest(
        system="system",
        messages=[ProviderInputItem.business_user("hello")],
        tools=[
            ProviderToolSpec(
                function=ProviderFunctionSpec(
                    name="lookup",
                    description="Lookup.",
                    parameters={"type": "object"},
                ),
            ),
        ],
    )

    assert request.messages == (ProviderInputItem.business_user("hello"),)
    assert request.tools == (
        ProviderToolSpec(
            function=ProviderFunctionSpec(
                name="lookup",
                description="Lookup.",
                parameters={"type": "object"},
            ),
        ),
    )


def test_provider_request_rejects_non_provider_input_messages() -> None:
    with pytest.raises(TypeError, match="ProviderInputItem"):
        ProviderRequest(
            system="system",
            messages=({"role": "user", "content": "hello"},),  # type: ignore[arg-type]
        )


def test_provider_request_builder_returns_provider_input_items() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    messages.append_user("hello")
    messages.append_assistant(
        "need tool",
        tool_calls=[
            ToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "README.md"},
            ),
        ],
    )

    request = ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        tools=[],
        snapshot_renderer=ContextSnapshotRenderer(HeuristicTokenCounter()),
        context_projections=type(
            "RuntimeProjectionProvider",
            (),
            {"projections": lambda self: project_context_state(context.snapshot())},
        )(),
    ).build().request

    assert all(type(message) is ProviderInputItem for message in request.messages)
    assert [message.kind for message in request.messages] == [
        "context_snapshot",
        "business_message",
        "business_message",
    ]
    assert request.messages[1].content[0].text == "hello"  # type: ignore[union-attr]
    assert request.messages[2].tool_calls == (
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "README.md"},
        ),
    )
