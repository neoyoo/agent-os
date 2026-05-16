import json
from dataclasses import dataclass
from typing import Any

from agentos.providers._content_parts import openai_chat_user_content
from agentos.providers._tool_arguments import (
    parse_json_object_arguments,
    require_tool_call_id,
    require_tool_call_name,
)
from agentos.providers.base import (
    ProviderMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)
from agentos.providers.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
    provider_tool_spec_to_dict,
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
                *[self._message(message) for message in request.messages],
            ],
            tools=(
                [provider_tool_spec_to_dict(tool) for tool in request.tools]
                if request.tools
                else None
            ),
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
            if not isinstance(
                message,
                (UserMessage, AssistantMessage, ToolResultMessage),
            ):
                raise ValueError(
                    "active messages must not include system role; use "
                    "ProviderRequest.system",
                )

    def _message(self, message: ProviderMessage) -> dict[str, object]:
        """把 provider message 转为 OpenAI chat message。"""

        if isinstance(message, UserMessage):
            return {"role": "user", "content": self._user_content(message.content)}
        if isinstance(message, AssistantMessage):
            result: dict[str, object] = {
                "role": "assistant",
                "content": message.content,
            }
            if message.tool_calls:
                result["content"] = message.content or None
                result["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(
                                tool_call.arguments,
                                ensure_ascii=False,
                            ),
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            return result
        if isinstance(message, ToolResultMessage):
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        raise ValueError(
            "active messages must not include system role; use ProviderRequest.system",
        )

    def _user_content(self, content: object) -> object:
        """把 canonical content parts 转为 OpenAI Chat content。"""

        if isinstance(content, str):
            return content
        if isinstance(content, tuple):
            return openai_chat_user_content(content)
        return content

    def _tool_calls(self, raw_tool_calls: list[object]) -> list[ProviderToolCall]:
        """把 OpenAI tool_calls 标准化为 ProviderToolCall。"""

        tool_calls: list[ProviderToolCall] = []
        for raw_tool_call in raw_tool_calls:
            arguments = raw_tool_call.function.arguments or "{}"
            tool_call_id = require_tool_call_id(
                raw_tool_call.id,
                provider_name="OpenAI",
            )
            tool_call_name = require_tool_call_name(
                raw_tool_call.function.name,
                provider_name="OpenAI",
            )
            tool_calls.append(
                ProviderToolCall(
                    id=tool_call_id,
                    name=tool_call_name,
                    arguments=parse_json_object_arguments(
                        arguments,
                        provider_name="OpenAI",
                    ),
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
