import json
from dataclasses import dataclass
from typing import Any

from agentos.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
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
            usage=self._usage(getattr(response, "usage", None)),
            model=getattr(response, "model", None) or self.model,
            provider_name="openai",
            response_id=getattr(response, "id", None),
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

    def _usage(self, raw_usage: object | None) -> ProviderUsage | None:
        """把 OpenAI usage 标准化。"""

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
