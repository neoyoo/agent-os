"""Anthropic Messages API Provider。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.providers._tool_arguments import (
    require_tool_call_id,
    require_tool_call_name,
)
from agentos.providers._timeout import raise_for_provider_timeout
from agentos.providers.anthropic_wire import (
    anthropic_tools,
    build_anthropic_messages,
)
from agentos.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)


@dataclass(slots=True)
class AnthropicProvider:
    """使用注入 client 的 Anthropic Messages Provider。"""

    client: Any
    model: str
    max_tokens: int = 4096
    timeout_seconds: float | None = None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用注入的 Anthropic client，并标准化完整响应。"""

        kwargs: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": request.system,
            "messages": build_anthropic_messages(request.messages),
            "tools": anthropic_tools(request.tools) or None,
        }
        if self.timeout_seconds is not None:
            kwargs["timeout"] = self.timeout_seconds
        try:
            response = self.client.messages.create(**kwargs)
        except Exception as error:
            raise_for_provider_timeout(error, provider_name="Anthropic")
            raise
        text_parts: list[str] = []
        tool_calls: list[ProviderToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                arguments = block.input
                if not isinstance(arguments, dict):
                    raise ValueError("Anthropic tool arguments must be an object")
                tool_calls.append(
                    ProviderToolCall(
                        id=require_tool_call_id(
                            block.id,
                            provider_name="Anthropic",
                        ),
                        name=require_tool_call_name(
                            block.name,
                            provider_name="Anthropic",
                        ),
                        arguments=arguments,
                    ),
                )
        return ProviderResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=getattr(response, "stop_reason", None),
            usage=_usage(getattr(response, "usage", None)),
            model=getattr(response, "model", None) or self.model,
            provider_name="anthropic",
            response_id=getattr(response, "id", None),
        )


def _usage(raw_usage: object | None) -> ProviderUsage | None:
    if raw_usage is None:
        return None
    return ProviderUsage(
        input_tokens=getattr(raw_usage, "input_tokens", None),
        output_tokens=getattr(raw_usage, "output_tokens", None),
        cached_input_tokens=getattr(raw_usage, "cache_read_input_tokens", None),
        cache_creation_input_tokens=getattr(
            raw_usage,
            "cache_creation_input_tokens",
            None,
        ),
    )
