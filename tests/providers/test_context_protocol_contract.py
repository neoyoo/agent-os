from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

import pytest

from agentos.providers import (
    ImagePart,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    TextPart,
)
from agentos.providers.openai_chat import OpenAIChatCompletionsProvider
from tests._provider_binary import binary_payload


@dataclass(frozen=True, slots=True)
class ContractObservation:
    system: str
    sequence: tuple[str, ...]


AdapterFactory = Callable[[ProviderRequest], ContractObservation]


def _contract_request() -> ProviderRequest:
    tool_call = ProviderToolCall(
        id="call_1",
        name="load_attachment",
        arguments={"handle": "art_1"},
    )
    payload = binary_payload(
        handle="art_1",
        filename="drawing.png",
        media_type="image/png",
        data=b"drawing-bytes",
    )
    return ProviderRequest(
        system="trusted-system",
        messages=(
            ProviderInputItem.context_snapshot("<context-snapshot/>"),
            ProviderInputItem.business_user("business-user"),
            ProviderInputItem.business_assistant("", (tool_call,)),
            ProviderInputItem.tool_result("call_1", "tool-result"),
            ProviderInputItem.context_mount(
                (TextPart("mount-marker"), ImagePart(payload)),
            ),
        ),
    )


def _compatible_factory(request: ProviderRequest) -> ContractObservation:
    class RecordingTransport:
        payload: dict[str, object] | None = None

        def post_json(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ) -> dict[str, object]:
            self.payload = payload
            return {"choices": [{"message": {"content": "done"}}]}

    transport = RecordingTransport()
    provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        transport=transport,
    )
    provider.complete(request)
    assert transport.payload is not None
    messages = transport.payload["messages"]
    assert isinstance(messages, list)
    system = messages[0]
    assert isinstance(system, dict)
    return ContractObservation(
        system=str(system["content"]),
        sequence=tuple(_compatible_marker(item) for item in messages[1:]),
    )


def _chat_factory(request: ProviderRequest) -> ContractObservation:
    class RecordingCompletions:
        payload: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.payload = kwargs
            return SimpleNamespace(
                id="chatcmpl_1",
                model="model",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="done", tool_calls=[]),
                    ),
                ],
            )

    completions = RecordingCompletions()
    provider = OpenAIChatCompletionsProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="model",
    )
    provider.complete(request)
    assert completions.payload is not None
    messages = completions.payload["messages"]
    assert isinstance(messages, list)
    system = messages[0]
    assert isinstance(system, dict)
    return ContractObservation(
        system=str(system["content"]),
        sequence=tuple(_compatible_marker(item) for item in messages[1:]),
    )


def _compatible_marker(item: object) -> str:
    assert isinstance(item, dict)
    role = item.get("role")
    content = item.get("content")
    if role == "assistant" and item.get("tool_calls"):
        return "assistant-tool-call"
    if role == "tool":
        return "tool-result"
    if content == "<context-snapshot/>":
        return "context-snapshot"
    if content == "business-user":
        return "business-user"
    if isinstance(content, list) and content[0] == {
        "type": "text",
        "text": "mount-marker",
    }:
        return "context-mount"
    raise AssertionError(item)


def _responses_factory(request: ProviderRequest) -> ContractObservation:
    class RecordingResponses:
        payload: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.payload = kwargs
            return SimpleNamespace(
                id="resp_1",
                model="model",
                status="completed",
                usage=None,
                output=[],
            )

    responses = RecordingResponses()
    provider = OpenAIProvider(
        client=SimpleNamespace(responses=responses),
        model="model",
    )
    provider.complete(request)
    assert responses.payload is not None
    input_items = responses.payload["input"]
    assert isinstance(input_items, list)
    return ContractObservation(
        system=str(responses.payload["instructions"]),
        sequence=tuple(_responses_marker(item) for item in input_items),
    )


def _responses_marker(item: object) -> str:
    assert isinstance(item, dict)
    item_type = item.get("type")
    content = item.get("content")
    if item_type == "function_call":
        return "assistant-tool-call"
    if item_type == "function_call_output":
        return "tool-result"
    if content == "<context-snapshot/>":
        return "context-snapshot"
    if content == "business-user":
        return "business-user"
    if isinstance(content, list) and content[0] == {
        "type": "input_text",
        "text": "mount-marker",
    }:
        return "context-mount"
    raise AssertionError(item)


PROVIDER_ADAPTER_FACTORIES: tuple[object, ...] = (
    pytest.param(_compatible_factory, id="openai-compatible"),
    pytest.param(_chat_factory, id="openai-chat-completions"),
    pytest.param(_responses_factory, id="openai-responses"),
)


@pytest.mark.parametrize("adapter_factory", PROVIDER_ADAPTER_FACTORIES)
def test_adapter_preserves_context_protocol_boundaries(
    adapter_factory: AdapterFactory,
) -> None:
    request = _contract_request()
    original = deepcopy(request)

    observation = adapter_factory(request)

    assert observation.system == "trusted-system"
    assert observation.sequence == (
        "context-snapshot",
        "business-user",
        "assistant-tool-call",
        "tool-result",
        "context-mount",
    )
    assert request == original
    image = request.messages[-1].content[-1]
    assert isinstance(image, ImagePart)
    assert image.payload.data == b"drawing-bytes"
