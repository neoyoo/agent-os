import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agentos.providers.base import (
    ProviderMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)


class OpenAICompatibleProviderError(RuntimeError):
    """OpenAI-compatible provider 请求失败。"""


class OpenAICompatibleTransport(Protocol):
    """OpenAI-compatible JSON HTTP transport。"""

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        """发送 JSON POST 并返回 JSON object。"""


class UrlLibJSONTransport:
    """基于标准库 urllib 的 JSON transport。"""

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        """发送 JSON POST 请求。"""

        request = Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = response.read().decode("utf-8")
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed with HTTP {error.code}: {body}",
            ) from error
        except URLError as error:
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed: {error.reason}",
            ) from error
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI-compatible response must be a JSON object")
        return parsed


@dataclass(slots=True)
class OpenAICompatibleProvider:
    """使用 OpenAI chat completions 协议的 provider。"""

    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0
    transport: OpenAICompatibleTransport | None = None
    thinking: dict[str, object] | None = None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用 OpenAI-compatible `/chat/completions` 并标准化响应。"""

        transport = self.transport or UrlLibJSONTransport()
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                *[self._message(message) for message in request.messages],
            ],
        }
        if request.tools:
            payload["tools"] = request.tools
        if self.thinking is not None:
            payload["thinking"] = dict(self.thinking)

        try:
            response = transport.post_json(
                url=self._chat_completions_url(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout=self.timeout,
            )
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed with HTTP {error.code}: {body}",
            ) from error
        return self._response(response)

    def _chat_completions_url(self) -> str:
        """返回 chat completions endpoint URL。"""

        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def _message(self, message: ProviderMessage) -> dict[str, object]:
        """把 SDK 内部 provider message 转为 OpenAI-compatible message。"""

        role = str(message["role"])
        if role == "system":
            raise OpenAICompatibleProviderError(
                "active messages must not include system role; use "
                "ProviderRequest.system",
            )
        result: dict[str, object] = {
            "role": role,
            "content": message.get("content", ""),
        }
        if role == "assistant" and message.get("tool_calls"):
            result["content"] = message.get("content") or None
            result["tool_calls"] = [
                self._request_tool_call(tool_call)
                for tool_call in message["tool_calls"]  # type: ignore[index]
            ]
        if role == "tool" and message.get("tool_call_id") is not None:
            result["tool_call_id"] = message["tool_call_id"]
        return result

    def _request_tool_call(self, tool_call: object) -> dict[str, object]:
        """把内部 tool call 摘要转为 OpenAI function tool_call。"""

        if not isinstance(tool_call, dict):
            raise ValueError("provider tool_call message must be an object")
        return {
            "id": tool_call["id"],
            "type": "function",
            "function": {
                "name": tool_call["name"],
                "arguments": json.dumps(
                    tool_call.get("arguments", {}),
                    ensure_ascii=False,
                ),
            },
        }

    def _response(self, response: dict[str, object]) -> ProviderResponse:
        """把 OpenAI-compatible response 转为 ProviderResponse。"""

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenAI-compatible response requires choices")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("OpenAI-compatible choice must be an object")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenAI-compatible choice requires message")
        raw_finish_reason = first_choice.get("finish_reason")
        return ProviderResponse(
            content=str(message.get("content") or ""),
            tool_calls=self._response_tool_calls(message.get("tool_calls") or []),
            stop_reason=(
                None if raw_finish_reason is None else str(raw_finish_reason)
            ),
            usage=self._usage(response.get("usage")),
            model=None if response.get("model") is None else str(response.get("model")),
            provider_name="openai-compatible",
            response_id=None if response.get("id") is None else str(response.get("id")),
        )

    def _response_tool_calls(self, raw_tool_calls: object) -> list[ProviderToolCall]:
        """解析 OpenAI-compatible response tool_calls。"""

        if not isinstance(raw_tool_calls, list):
            raise ValueError("OpenAI-compatible tool_calls must be a list")
        tool_calls: list[ProviderToolCall] = []
        for raw_tool_call in raw_tool_calls:
            if not isinstance(raw_tool_call, dict):
                raise ValueError("OpenAI-compatible tool_call must be an object")
            function = raw_tool_call.get("function")
            if not isinstance(function, dict):
                raise ValueError("OpenAI-compatible tool_call requires function")
            arguments = function.get("arguments") or "{}"
            if not isinstance(arguments, str):
                raise ValueError("OpenAI-compatible tool arguments must be a string")
            tool_calls.append(
                ProviderToolCall(
                    id=str(raw_tool_call["id"]),
                    name=str(function["name"]),
                    arguments=json.loads(arguments),
                ),
            )
        return tool_calls

    def _usage(self, raw_usage: object) -> ProviderUsage | None:
        """把 OpenAI-compatible JSON usage 标准化。"""

        if not isinstance(raw_usage, dict):
            return None
        prompt_details = raw_usage.get("prompt_tokens_details")
        completion_details = raw_usage.get("completion_tokens_details")
        return ProviderUsage(
            input_tokens=self._int_or_none(raw_usage.get("prompt_tokens")),
            output_tokens=self._int_or_none(raw_usage.get("completion_tokens")),
            total_tokens=self._int_or_none(raw_usage.get("total_tokens")),
            cached_input_tokens=(
                self._int_or_none(prompt_details.get("cached_tokens"))
                if isinstance(prompt_details, dict)
                else None
            ),
            reasoning_output_tokens=(
                self._int_or_none(completion_details.get("reasoning_tokens"))
                if isinstance(completion_details, dict)
                else None
            ),
        )

    def _int_or_none(self, value: object) -> int | None:
        """把 provider usage 数值转为 int。"""

        if value is None:
            return None
        return int(value)
