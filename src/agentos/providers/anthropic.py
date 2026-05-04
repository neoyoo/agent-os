from dataclasses import dataclass
from typing import Any

from agentos.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)


@dataclass(slots=True)
class AnthropicProvider:
    """Anthropic messages 薄适配器，client 由调用方注入。"""

    client: Any
    model: str

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用注入的 Anthropic client，并标准化响应。"""

        self._ensure_no_active_system_messages(request)
        response = self.client.messages.create(
            model=self.model,
            system=request.system,
            messages=request.messages,
            tools=self._tools(request.tools) or None,
        )
        text_parts: list[str] = []
        tool_calls: list[ProviderToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ProviderToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input),
                    ),
                )
        return ProviderResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=getattr(response, "stop_reason", None),
        )

    def _ensure_no_active_system_messages(self, request: ProviderRequest) -> None:
        """拒绝 active window 中的 system 消息，避免 provider 收到双 system。"""

        for message in request.messages:
            if message.get("role") == "system":
                raise ValueError(
                    "active messages must not include system role; use "
                    "ProviderRequest.system",
                )

    def _tools(self, tools: list[ProviderToolSpec]) -> list[dict[str, object]]:
        """把内部 function tool schema 转成 Anthropic input_schema 形态。"""

        converted: list[dict[str, object]] = []
        for tool in tools:
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            converted.append(
                {
                    "name": function["name"],
                    "description": function["description"],
                    "input_schema": function["parameters"],
                },
            )
        return converted
