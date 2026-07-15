from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agentos.providers import (
    AnthropicProvider,
    ImagePart,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    TextPart,
)
from agentos.providers.anthropic_wire import (
    anthropic_message,
    merge_adjacent_user_messages,
)
from tests._provider_binary import binary_payload


class RecordingMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            id="msg_1",
            model="claude-test",
            stop_reason="end_turn",
            usage=None,
            content=[SimpleNamespace(type="text", text="done")],
        )


def _logical_messages() -> tuple[ProviderInputItem, ...]:
    tool_call = ProviderToolCall(
        id="call_1",
        name="lookup",
        arguments={"query": "agentos"},
    )
    return (
        ProviderInputItem.business_user("hello"),
        ProviderInputItem.business_assistant("", (tool_call,)),
        ProviderInputItem.tool_result("call_1", "done"),
        ProviderInputItem.recalled_user("old"),
        ProviderInputItem.recalled_tool("call_1", "old result"),
    )


def _provider_input_with_content(
    role: str,
    content: tuple[TextPart | ImagePart, ...],
) -> ProviderInputItem:
    if role == "assistant":
        return ProviderInputItem(
            role="assistant",
            kind="business_message",
            origin="message_store",
            authority="conversation_data",
            persistence="stored",
            visibility="conversation",
            content=content,
        )
    return ProviderInputItem(
        role="tool",
        kind="tool_result",
        origin="message_store",
        authority="tool_data",
        persistence="stored",
        visibility="internal",
        content=content,
        tool_call_id="call_1",
    )


@pytest.mark.parametrize("logical", _logical_messages())
def test_anthropic_wire_excludes_internal_metadata(
    logical: ProviderInputItem,
) -> None:
    wire = anthropic_message(logical)

    assert {
        "origin",
        "authority",
        "persistence",
        "visibility",
    }.isdisjoint(wire)


@pytest.mark.parametrize("role", ["assistant", "tool"])
@pytest.mark.parametrize(
    "content",
    [
        pytest.param((), id="empty"),
        pytest.param((TextPart("one"), TextPart("two")), id="multiple"),
        pytest.param((ImagePart(binary_payload()),), id="non-text"),
    ],
)
def test_anthropic_rejects_non_single_text_assistant_and_tool_content(
    role: str,
    content: tuple[TextPart | ImagePart, ...],
) -> None:
    logical = _provider_input_with_content(role, content)

    with pytest.raises(
        ValueError,
        match=rf"{role} provider input content requires exactly one TextPart",
    ):
        anthropic_message(logical)


def test_anthropic_context_mount_maps_to_multimodal_user() -> None:
    payload = binary_payload(
        handle="art_1",
        filename="diagram.png",
        media_type="image/png",
        data=b"image-bytes",
    )

    wire = anthropic_message(
        ProviderInputItem.context_mount(
            (TextPart("inspect"), ImagePart(payload)),
        ),
    )

    assert wire["role"] == "user"
    assert isinstance(wire["content"], list)
    assert wire["content"][0] == {"type": "text", "text": "inspect"}


def test_anthropic_merges_all_adjacent_user_content_without_request_mutation() -> None:
    messages = RecordingMessages()
    provider = AnthropicProvider(
        client=SimpleNamespace(messages=messages),
        model="claude-test",
    )
    tool_call = ProviderToolCall(
        id="call_1",
        name="load_attachment",
        arguments={"handle": "art_1"},
    )
    image = binary_payload(
        handle="art_1",
        filename="drawing.png",
        media_type="image/png",
        data=b"image-bytes",
    )
    request = ProviderRequest(
        system="trusted-system",
        messages=(
            ProviderInputItem.context_snapshot("<context-snapshot/>"),
            ProviderInputItem.business_user("inspect the drawing"),
            ProviderInputItem.business_assistant("", (tool_call,)),
            ProviderInputItem.tool_result("call_1", "attachment loaded"),
            ProviderInputItem.tool_result("call_2", "metadata loaded"),
            ProviderInputItem.context_mount(
                (TextPart("mounted content"), ImagePart(image, detail="high")),
            ),
        ),
    )
    original = deepcopy(request)

    provider.complete(request)

    assert messages.kwargs is not None
    assert messages.kwargs["system"] == "trusted-system"
    assert messages.kwargs["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<context-snapshot/>"},
                {"type": "text", "text": "inspect the drawing"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "load_attachment",
                    "input": {"handle": "art_1"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "attachment loaded",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call_2",
                    "content": "metadata loaded",
                },
                {"type": "text", "text": "mounted content"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aW1hZ2UtYnl0ZXM=",
                    },
                },
            ],
        },
    ]
    assert request == original
    assert image.data == b"image-bytes"


def test_anthropic_user_merge_does_not_mutate_created_wire_objects() -> None:
    wire = [
        {"role": "user", "content": "first"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "second"}],
        },
    ]
    original = deepcopy(wire)

    merged = merge_adjacent_user_messages(wire)

    assert merged == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        },
    ]
    assert wire == original
    assert merged[0] is not wire[0]
    assert merged[0]["content"] is not wire[1]["content"]


@pytest.mark.parametrize(
    "block",
    [
        SimpleNamespace(type="tool_use", id=None, name="lookup", input={}),
        SimpleNamespace(type="tool_use", id="call_1", name=None, input={}),
        SimpleNamespace(type="tool_use", id="call_1", name="lookup", input=[]),
    ],
)
def test_anthropic_rejects_malformed_tool_calls(block: object) -> None:
    class MalformedMessages:
        def create(self, **kwargs: object) -> object:
            return SimpleNamespace(
                id="msg_1",
                model="model",
                stop_reason="tool_use",
                usage=None,
                content=[block],
            )

    provider = AnthropicProvider(
        client=SimpleNamespace(messages=MalformedMessages()),
        model="model",
    )

    with pytest.raises(ValueError, match="Anthropic tool"):
        provider.complete(ProviderRequest(system="system", messages=()))
