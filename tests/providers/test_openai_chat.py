from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agentos.providers import (
    FilePart,
    ImagePart,
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
    TextPart,
)
from agentos.providers.openai_chat import OpenAIChatCompletionsProvider
from tests._provider_binary import binary_payload


class RecordingCompletions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return self.response


def completed_response(
    *,
    content: str | None = "done",
    tool_calls: list[object] | None = None,
) -> object:
    return SimpleNamespace(
        id="chatcmpl_1",
        model="gpt-test",
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=content,
                    tool_calls=[] if tool_calls is None else tool_calls,
                ),
            ),
        ],
    )


def provider_for(
    response: object,
    *,
    timeout_seconds: float | None = None,
) -> tuple[OpenAIChatCompletionsProvider, RecordingCompletions]:
    completions = RecordingCompletions(response)
    provider = OpenAIChatCompletionsProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="gpt-test",
        timeout_seconds=timeout_seconds,
    )
    return provider, completions


def function_tool() -> ProviderToolSpec:
    return ProviderToolSpec(
        function=ProviderFunctionSpec(
            name="load_attachment",
            description="Load an attachment.",
            parameters={
                "type": "object",
                "properties": {"handle": {"type": "string"}},
                "required": ["handle"],
            },
        ),
    )


def test_openai_chat_maps_ordered_messages_tools_and_image_without_mutation() -> None:
    provider, completions = provider_for(completed_response())
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
            ProviderInputItem.context_mount(
                (TextPart("mounted content"), ImagePart(image, detail="high")),
            ),
        ),
        tools=(function_tool(),),
        parallel_tool_calls=False,
    )
    original = deepcopy(request)

    provider.complete(request)

    assert completions.kwargs == {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "trusted-system"},
            {"role": "user", "content": "<context-snapshot/>"},
            {"role": "user", "content": "inspect the drawing"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "load_attachment",
                            "arguments": '{"handle": "art_1"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "attachment loaded",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "mounted content"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,aW1hZ2UtYnl0ZXM=",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "load_attachment",
                    "description": "Load an attachment.",
                    "parameters": {
                        "type": "object",
                        "properties": {"handle": {"type": "string"}},
                        "required": ["handle"],
                    },
                },
            },
        ],
        "parallel_tool_calls": False,
    }
    assert request == original
    assert image.data == b"image-bytes"


def test_openai_chat_omits_tool_fields_without_tools_and_forwards_timeout() -> None:
    provider, completions = provider_for(
        completed_response(),
        timeout_seconds=7.5,
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=(ProviderInputItem.business_user("hello"),),
            parallel_tool_calls=True,
        ),
    )

    assert completions.kwargs is not None
    assert completions.kwargs["timeout"] == 7.5
    assert "tools" not in completions.kwargs
    assert "parallel_tool_calls" not in completions.kwargs


def test_openai_chat_forwards_parallel_tool_calls_true_with_tools() -> None:
    provider, completions = provider_for(completed_response())

    provider.complete(
        ProviderRequest(
            system="system",
            messages=(),
            tools=(function_tool(),),
            parallel_tool_calls=True,
        ),
    )

    assert completions.kwargs is not None
    assert completions.kwargs["parallel_tool_calls"] is True


def test_openai_chat_normalizes_text_tool_calls_and_usage() -> None:
    response = completed_response(
        content="Need the file.",
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(
                    name="load_attachment",
                    arguments='{"handle": "art_1"}',
                ),
            ),
        ],
    )
    response.choices[0].finish_reason = "tool_calls"
    response.usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        prompt_tokens_details=SimpleNamespace(cached_tokens=2),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
    )
    provider, _ = provider_for(response)

    result = provider.complete(ProviderRequest(system="system", messages=()))

    assert result.content == "Need the file."
    assert result.tool_calls == (
        ProviderToolCall(
            id="call_1",
            name="load_attachment",
            arguments={"handle": "art_1"},
        ),
    )
    assert result.stop_reason == "tool_calls"
    assert result.usage == ProviderUsage(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cached_input_tokens=2,
        reasoning_output_tokens=1,
    )
    assert result.model == "gpt-test"
    assert result.provider_name == "openai"
    assert result.response_id == "chatcmpl_1"


@pytest.mark.parametrize(
    "arguments",
    ['["not", "an", "object"]', "not-json"],
)
def test_openai_chat_rejects_malformed_tool_arguments(arguments: str) -> None:
    provider, _ = provider_for(
        completed_response(
            tool_calls=[
                SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(name="lookup", arguments=arguments),
                ),
            ],
        ),
    )

    with pytest.raises(ValueError, match="OpenAI Chat tool arguments"):
        provider.complete(ProviderRequest(system="system", messages=()))


def test_openai_chat_rejects_missing_tool_identity() -> None:
    provider, _ = provider_for(
        completed_response(
            tool_calls=[
                SimpleNamespace(
                    id=None,
                    function=SimpleNamespace(name="lookup", arguments="{}"),
                ),
            ],
        ),
    )

    with pytest.raises(ValueError, match="OpenAI Chat tool_call requires id"):
        provider.complete(ProviderRequest(system="system", messages=()))


def test_openai_chat_rejects_file_parts() -> None:
    provider, _ = provider_for(completed_response())
    document = binary_payload(
        handle="art_1",
        filename="drawing.pdf",
        media_type="application/pdf",
        data=b"pdf data",
    )

    with pytest.raises(ValueError, match="does not support file attachments"):
        provider.complete(
            ProviderRequest(
                system="system",
                messages=(
                    ProviderInputItem.context_mount((FilePart(document),)),
                ),
            ),
        )


def test_openai_chat_rejects_malformed_choices() -> None:
    response = completed_response()
    response.choices = []
    provider, _ = provider_for(response)

    with pytest.raises(ValueError, match="requires one choice"):
        provider.complete(ProviderRequest(system="system", messages=()))
