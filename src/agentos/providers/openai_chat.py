"""官方 OpenAI Chat Completions Provider。"""

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
from agentos.providers.openai_chat_wire import build_openai_chat_payload


@dataclass(slots=True)
class OpenAIChatCompletionsProvider:
    """使用官方 Chat Completions API 的 OpenAI Provider。"""

    client: Any
    model: str
    timeout_seconds: float | None = None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用注入的 Chat Completions client 并标准化完整响应。"""

        kwargs = build_openai_chat_payload(
            model=self.model,
            request=request,
            include_parallel_tool_calls=True,
        )
        if self.timeout_seconds is not None:
            kwargs["timeout"] = self.timeout_seconds
        response = self.client.chat.completions.create(**kwargs)
        choice = _first_choice(response)
        message = getattr(choice, "message", None)
        if message is None:
            raise ValueError("OpenAI Chat choice requires message")
        content = getattr(message, "content", None)
        if content is not None and not isinstance(content, str):
            raise ValueError("OpenAI Chat message content must be text or None")
        return ProviderResponse(
            content=content or "",
            tool_calls=_tool_calls(getattr(message, "tool_calls", None)),
            stop_reason=getattr(choice, "finish_reason", None),
            usage=_usage(getattr(response, "usage", None)),
            model=getattr(response, "model", None) or self.model,
            provider_name="openai",
            response_id=getattr(response, "id", None),
        )


def _first_choice(response: object) -> object:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or not choices:
        raise ValueError("OpenAI Chat response requires one choice")
    return choices[0]


def _tool_calls(raw_tool_calls: object) -> tuple[ProviderToolCall, ...]:
    if raw_tool_calls is None:
        return ()
    if not isinstance(raw_tool_calls, (list, tuple)):
        raise ValueError("OpenAI Chat tool_calls must be a sequence")
    result: list[ProviderToolCall] = []
    for raw_tool_call in raw_tool_calls:
        function = getattr(raw_tool_call, "function", None)
        if function is None:
            raise ValueError("OpenAI Chat tool_call requires function")
        arguments = getattr(function, "arguments", None)
        if not isinstance(arguments, str):
            raise ValueError("OpenAI Chat tool arguments must be valid JSON")
        result.append(
            ProviderToolCall(
                id=require_tool_call_id(
                    getattr(raw_tool_call, "id", None),
                    provider_name="OpenAI Chat",
                ),
                name=require_tool_call_name(
                    getattr(function, "name", None),
                    provider_name="OpenAI Chat",
                ),
                arguments=parse_json_object_arguments(
                    arguments,
                    provider_name="OpenAI Chat",
                ),
            ),
        )
    return tuple(result)


def _usage(raw_usage: object | None) -> ProviderUsage | None:
    if raw_usage is None:
        return None
    prompt_details = getattr(raw_usage, "prompt_tokens_details", None)
    completion_details = getattr(raw_usage, "completion_tokens_details", None)
    return ProviderUsage(
        input_tokens=getattr(raw_usage, "prompt_tokens", None),
        output_tokens=getattr(raw_usage, "completion_tokens", None),
        total_tokens=getattr(raw_usage, "total_tokens", None),
        cached_input_tokens=(
            None
            if prompt_details is None
            else getattr(prompt_details, "cached_tokens", None)
        ),
        reasoning_output_tokens=(
            None
            if completion_details is None
            else getattr(completion_details, "reasoning_tokens", None)
        ),
    )
