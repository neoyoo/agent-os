import json
from dataclasses import dataclass
from typing import Any

from agentos.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)


@dataclass(slots=True)
class OpenAIProvider:
    """OpenAI chat completions 薄适配器，client 由调用方注入。"""

    client: Any
    model: str

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用注入的 OpenAI client，并标准化响应。"""

        self._ensure_no_active_system_messages(request)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": request.system},
                *request.messages,
            ],
            tools=request.tools or None,
        )
        choice = response.choices[0]
        message = choice.message
        return ProviderResponse(
            content=message.content or "",
            tool_calls=self._tool_calls(getattr(message, "tool_calls", None) or []),
            stop_reason=getattr(choice, "finish_reason", None),
        )

    def _ensure_no_active_system_messages(self, request: ProviderRequest) -> None:
        """拒绝 active window 中的 system 消息，避免 provider 收到双 system。"""

        for message in request.messages:
            if message.get("role") == "system":
                raise ValueError(
                    "active messages must not include system role; use "
                    "ProviderRequest.system",
                )

    def _tool_calls(self, raw_tool_calls: list[object]) -> list[ProviderToolCall]:
        """把 OpenAI tool_calls 标准化为 ProviderToolCall。"""

        tool_calls: list[ProviderToolCall] = []
        for raw_tool_call in raw_tool_calls:
            arguments = raw_tool_call.function.arguments or "{}"
            tool_calls.append(
                ProviderToolCall(
                    id=raw_tool_call.id,
                    name=raw_tool_call.function.name,
                    arguments=json.loads(arguments),
                ),
            )
        return tool_calls
