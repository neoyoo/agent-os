"""OpenAI Responses API Provider。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.providers._tool_arguments import (
    parse_json_object_arguments,
    require_tool_call_id,
    require_tool_call_name,
)
from agentos.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)
from agentos.providers.openai_responses_wire import (
    build_openai_responses_payload,
)


@dataclass(slots=True)
class OpenAIProvider:
    """使用官方 Responses API 的 OpenAI Provider。"""

    client: Any
    model: str
    timeout_seconds: float | None = None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用注入的 Responses client 并标准化完整响应。"""

        kwargs = build_openai_responses_payload(model=self.model, request=request)
        if self.timeout_seconds is not None:
            kwargs["timeout"] = self.timeout_seconds
        response = self.client.responses.create(**kwargs)
        content, tool_calls = _response_output(response)
        status = getattr(response, "status", None)
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=status if isinstance(status, str) else None,
            usage=_usage(getattr(response, "usage", None)),
            model=getattr(response, "model", None) or self.model,
            provider_name="openai",
            response_id=getattr(response, "id", None),
        )


def _response_output(
    response: object,
) -> tuple[str, tuple[ProviderToolCall, ...]]:
    output = getattr(response, "output", None)
    if not isinstance(output, (list, tuple)):
        raise ValueError("OpenAI response output must be a sequence")
    text_parts: list[str] = []
    tool_calls: list[ProviderToolCall] = []
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            text_parts.extend(_message_text(item))
        elif item_type == "function_call":
            tool_calls.append(_tool_call(item))
    return "".join(text_parts), tuple(tool_calls)


def _message_text(message: object) -> tuple[str, ...]:
    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        raise ValueError("OpenAI response message content must be a sequence")
    result: list[str] = []
    for part in content:
        if getattr(part, "type", None) != "output_text":
            continue
        text = getattr(part, "text", None)
        if not isinstance(text, str):
            raise ValueError("OpenAI response output_text requires text")
        result.append(text)
    return tuple(result)


def _tool_call(raw_tool_call: object) -> ProviderToolCall:
    tool_call_id = require_tool_call_id(
        getattr(raw_tool_call, "call_id", None),
        provider_name="OpenAI",
    )
    tool_call_name = require_tool_call_name(
        getattr(raw_tool_call, "name", None),
        provider_name="OpenAI",
    )
    arguments = getattr(raw_tool_call, "arguments", None)
    if not isinstance(arguments, str):
        raise ValueError("OpenAI tool arguments must be valid JSON")
    return ProviderToolCall(
        id=tool_call_id,
        name=tool_call_name,
        arguments=parse_json_object_arguments(
            arguments,
            provider_name="OpenAI",
        ),
    )


def _usage(raw_usage: object | None) -> ProviderUsage | None:
    if raw_usage is None:
        return None
    input_details = getattr(raw_usage, "input_tokens_details", None)
    output_details = getattr(raw_usage, "output_tokens_details", None)
    return ProviderUsage(
        input_tokens=getattr(raw_usage, "input_tokens", None),
        output_tokens=getattr(raw_usage, "output_tokens", None),
        total_tokens=getattr(raw_usage, "total_tokens", None),
        cached_input_tokens=(
            None
            if input_details is None
            else getattr(input_details, "cached_tokens", None)
        ),
        reasoning_output_tokens=(
            None
            if output_details is None
            else getattr(output_details, "reasoning_tokens", None)
        ),
    )
