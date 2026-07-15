from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agentos.providers import (
    FilePart,
    ImagePart,
    OpenAIProvider,
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
    TextPart,
)
from tests._provider_binary import binary_payload


class RecordingResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return self.response


def completed_response(*, output: list[object] | None = None) -> object:
    return SimpleNamespace(
        id="resp_1",
        model="gpt-test",
        status="completed",
        usage=None,
        output=[] if output is None else output,
    )


def provider_for(
    response: object,
    *,
    timeout_seconds: float | None = None,
) -> tuple[OpenAIProvider, RecordingResponses]:
    responses = RecordingResponses(response)
    provider = OpenAIProvider(
        client=SimpleNamespace(responses=responses),
        model="gpt-test",
        timeout_seconds=timeout_seconds,
    )
    return provider, responses


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


def test_openai_responses_maps_ordered_items_tools_and_binary_without_mutation() -> None:
    provider, responses = provider_for(completed_response())
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
    document = binary_payload(
        handle="art_2",
        filename="drawing.pdf",
        media_type="application/pdf",
        data=b"pdf data",
    )
    request = ProviderRequest(
        system="trusted-system",
        messages=(
            ProviderInputItem.context_snapshot("<context-snapshot/>"),
            ProviderInputItem.business_user("inspect the drawing"),
            ProviderInputItem.business_assistant("", (tool_call,)),
            ProviderInputItem.tool_result("call_1", "attachment loaded"),
            ProviderInputItem.context_mount(
                (
                    TextPart("mounted content"),
                    ImagePart(image, detail="high"),
                    FilePart(document),
                ),
            ),
        ),
        tools=(function_tool(),),
        parallel_tool_calls=False,
    )
    original = deepcopy(request)

    provider.complete(request)

    assert responses.kwargs == {
        "model": "gpt-test",
        "instructions": "trusted-system",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": "<context-snapshot/>",
            },
            {
                "type": "message",
                "role": "user",
                "content": "inspect the drawing",
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "load_attachment",
                "arguments": '{"handle": "art_1"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "attachment loaded",
            },
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "mounted content"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,aW1hZ2UtYnl0ZXM=",
                        "detail": "high",
                    },
                    {
                        "type": "input_file",
                        "filename": "drawing.pdf",
                        "file_data": "data:application/pdf;base64,cGRmIGRhdGE=",
                    },
                ],
            },
        ],
        "tools": [
            {
                "type": "function",
                "name": "load_attachment",
                "description": "Load an attachment.",
                "parameters": {
                    "type": "object",
                    "properties": {"handle": {"type": "string"}},
                    "required": ["handle"],
                },
            },
        ],
        "parallel_tool_calls": False,
    }
    assert request == original
    assert image.data == b"image-bytes"
    assert document.data == b"pdf data"


def test_openai_responses_derives_pdf_filename_without_mutating_payload() -> None:
    provider, responses = provider_for(completed_response())
    document = binary_payload(
        handle="art_550e8400-e29b-41d4-a716-446655440000",
        filename=None,
        media_type="application/pdf",
        data=b"pdf data",
    )
    request = ProviderRequest(
        system="trusted-system",
        messages=(
            ProviderInputItem.context_mount((FilePart(document),)),
        ),
    )
    original = deepcopy(request)

    provider.complete(request)

    assert responses.kwargs is not None
    input_items = responses.kwargs["input"]
    assert input_items[0]["content"][0]["filename"] == (
        "art_550e8400-e29b-41d4-a716-446655440000.pdf"
    )
    assert document.filename is None
    assert request == original


def test_openai_responses_omits_tool_fields_without_tools_and_forwards_timeout() -> None:
    provider, responses = provider_for(
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

    assert responses.kwargs is not None
    assert responses.kwargs["timeout"] == 7.5
    assert "tools" not in responses.kwargs
    assert "parallel_tool_calls" not in responses.kwargs


def test_openai_responses_normalizes_text_tool_calls_and_usage() -> None:
    output = [
        SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(type="output_text", text="Need "),
                SimpleNamespace(type="output_text", text="the file."),
            ],
        ),
        SimpleNamespace(
            type="function_call",
            call_id="call_1",
            name="load_attachment",
            arguments='{"handle": "art_1"}',
        ),
    ]
    response = completed_response(output=output)
    response.usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=SimpleNamespace(cached_tokens=2),
        output_tokens_details=SimpleNamespace(reasoning_tokens=1),
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
    assert result.stop_reason == "completed"
    assert result.usage == ProviderUsage(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cached_input_tokens=2,
        reasoning_output_tokens=1,
    )
    assert result.model == "gpt-test"
    assert result.provider_name == "openai"
    assert result.response_id == "resp_1"


@pytest.mark.parametrize(
    "arguments",
    ['["not", "an", "object"]', "not-json"],
)
def test_openai_responses_rejects_malformed_tool_arguments(arguments: str) -> None:
    provider, _ = provider_for(
        completed_response(
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="lookup",
                    arguments=arguments,
                ),
            ],
        ),
    )

    with pytest.raises(ValueError, match="OpenAI tool arguments"):
        provider.complete(ProviderRequest(system="system", messages=()))


def test_openai_responses_rejects_missing_tool_identity() -> None:
    provider, _ = provider_for(
        completed_response(
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id=None,
                    name="lookup",
                    arguments="{}",
                ),
            ],
        ),
    )

    with pytest.raises(ValueError, match="OpenAI tool_call requires id"):
        provider.complete(ProviderRequest(system="system", messages=()))


def test_openai_responses_rejects_malformed_output_collection() -> None:
    response = completed_response()
    response.output = None
    provider, _ = provider_for(response)

    with pytest.raises(ValueError, match="OpenAI response output must be a sequence"):
        provider.complete(ProviderRequest(system="system", messages=()))
