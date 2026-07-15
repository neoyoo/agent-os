import json
from dataclasses import dataclass
from typing import Any

from agentos.providers.json_values import thaw_json
from agentos.providers._content_parts import openai_chat_user_content
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
from agentos.providers.input import ProviderInputItem, TextPart
from agentos.providers.tool_specs import provider_tool_spec_to_dict


@dataclass(slots=True)
class OpenAIProvider:
    """OpenAI chat completions 薄适配器，client 由调用方注入。"""

    client: Any
    model: str
    timeout_seconds: float | None = None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用注入的 OpenAI client，并标准化响应。"""

        self._ensure_no_active_system_messages(request)
        kwargs: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                *[self._message(message) for message in request.messages],
            ],
            "tools": (
                [provider_tool_spec_to_dict(tool) for tool in request.tools]
                if request.tools
                else None
            ),
        }
        if self.timeout_seconds is not None:
            kwargs["timeout"] = self.timeout_seconds
        if request.tools and request.parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = request.parallel_tool_calls
        response = self.client.chat.completions.create(**kwargs)
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
            if message.role in ("user", "assistant", "tool"):
                continue
            raise ValueError(
                "active messages must not include system role; use "
                "ProviderRequest.system",
            )

    def _message(
        self,
        message: ProviderInputItem,
    ) -> dict[str, object]:
        """把逻辑 Provider 输入转为 OpenAI chat message。"""

        if message.role == "user":
            content: object = message.content
            if len(message.content) == 1 and isinstance(message.content[0], TextPart):
                content = message.content[0].text
            return {"role": "user", "content": self._user_content(content)}
        if len(message.content) != 1 or not isinstance(message.content[0], TextPart):
            raise ValueError(
                f"{message.role} provider input content requires exactly one TextPart",
            )
        if message.role == "assistant":
            result: dict[str, object] = {
                "role": "assistant",
                "content": message.content[0].text,
            }
            if message.tool_calls:
                result["content"] = message.content[0].text or None
                result["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(
                                thaw_json(tool_call.arguments),
                                ensure_ascii=False,
                            ),
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            return result
        if message.role == "tool" and message.tool_call_id is not None:
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content[0].text,
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
