from dataclasses import FrozenInstanceError

import pytest

from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime, ToolCall
from agentos.providers import (
    AssistantMessage,
    ProviderFunctionSpec,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
    ToolResultMessage,
    UserMessage,
    provider_message_from_dict,
    provider_message_to_dict,
    provider_tool_spec_from_dict,
    provider_tool_spec_to_dict,
)
from agentos.runtime import ProviderRequestBuilder


def test_provider_messages_are_frozen_slotted_dataclasses() -> None:
    message = UserMessage(content="hello")

    with pytest.raises(FrozenInstanceError):
        message.content = "mutated"  # type: ignore[misc]

    assert not hasattr(message, "__dict__")


def test_provider_message_types_are_importable_from_agentos_root() -> None:
    from agentos import AssistantMessage as RootAssistantMessage
    from agentos import ProviderToolSpec as RootProviderToolSpec
    from agentos import UserMessage as RootUserMessage

    assert RootUserMessage is UserMessage
    assert RootAssistantMessage is AssistantMessage
    assert RootProviderToolSpec is ProviderToolSpec


def test_provider_message_round_trips_openai_style_dicts() -> None:
    assistant = AssistantMessage(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "README.md"},
            ),
        ),
    )

    as_dict = provider_message_to_dict(assistant)
    restored = provider_message_from_dict(as_dict)

    assert as_dict == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "name": "read_file",
                "arguments": {"path": "README.md"},
            },
        ],
    }
    assert restored == assistant
    assert provider_message_from_dict(
        {"role": "tool", "tool_call_id": "call_1", "content": "done"},
    ) == ToolResultMessage(tool_call_id="call_1", content="done")


def test_provider_message_null_content_normalizes_to_empty_string() -> None:
    assistant = provider_message_from_dict(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "read_file",
                    "arguments": {"path": "README.md"},
                },
            ],
        },
    )

    assert assistant == AssistantMessage(
        content="",
        tool_calls=(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "README.md"},
            ),
        ),
    )
    assert provider_message_from_dict({"role": "user", "content": None}) == UserMessage(
        content="",
    )
    assert provider_message_from_dict(
        {"role": "tool", "tool_call_id": "call_1", "content": None},
    ) == ToolResultMessage(tool_call_id="call_1", content="")


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


def test_provider_request_normalizes_legacy_dict_inputs() -> None:
    request = ProviderRequest(
        system="system",
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup.",
                    "parameters": {"type": "object"},
                },
            },
        ],
    )

    assert request.messages == [UserMessage(content="hello")]
    assert request.tools == [
        ProviderToolSpec(
            function=ProviderFunctionSpec(
                name="lookup",
                description="Lookup.",
                parameters={"type": "object"},
            ),
        ),
    ]


def test_provider_request_builder_returns_strong_typed_messages() -> None:
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
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[],
    ).build(context)

    assert request.messages == [
        UserMessage(content="hello"),
        AssistantMessage(
            content="need tool",
            tool_calls=(
                ProviderToolCall(
                    id="call_1",
                    name="read_file",
                    arguments={"path": "README.md"},
                ),
            ),
        ),
    ]
