"""OpenAI-compatible response 与 stream 解析。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from agentos.providers._tool_arguments import (
    parse_json_object_arguments,
    require_tool_call_id,
    require_tool_call_name,
)
from agentos.providers.base import ProviderResponse, ProviderToolCall, ProviderUsage
from agentos.providers.stream import (
    ProviderContentDelta,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderThinkingDelta,
    ProviderToolCallDelta,
)


def parse_openai_compatible_response(
    response: dict[str, object],
) -> ProviderResponse:
    """把 Chat Completions response 转为 ProviderResponse。"""

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
        tool_calls=parse_response_tool_calls(message.get("tool_calls") or []),
        stop_reason=None if raw_finish_reason is None else str(raw_finish_reason),
        usage=parse_openai_compatible_usage(response.get("usage")),
        model=None if response.get("model") is None else str(response.get("model")),
        provider_name="openai-compatible",
        response_id=None if response.get("id") is None else str(response.get("id")),
    )


def parse_response_tool_calls(raw_tool_calls: object) -> tuple[ProviderToolCall, ...]:
    """解析完整响应中的 function tool calls。"""

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
                id=require_tool_call_id(
                    raw_tool_call.get("id"),
                    provider_name="OpenAI-compatible",
                ),
                name=require_tool_call_name(
                    function.get("name"),
                    provider_name="OpenAI-compatible",
                ),
                arguments=parse_json_object_arguments(
                    arguments,
                    provider_name="OpenAI-compatible",
                ),
            ),
        )
    return tuple(tool_calls)


def parse_openai_compatible_usage(raw_usage: object) -> ProviderUsage | None:
    """把 OpenAI-compatible JSON usage 标准化。"""

    if not isinstance(raw_usage, dict):
        return None
    prompt_details = raw_usage.get("prompt_tokens_details")
    completion_details = raw_usage.get("completion_tokens_details")
    return ProviderUsage(
        input_tokens=_int_or_none(raw_usage.get("prompt_tokens")),
        output_tokens=_int_or_none(raw_usage.get("completion_tokens")),
        total_tokens=_int_or_none(raw_usage.get("total_tokens")),
        cached_input_tokens=(
            _int_or_none(prompt_details.get("cached_tokens"))
            if isinstance(prompt_details, dict)
            else None
        ),
        reasoning_output_tokens=(
            _int_or_none(completion_details.get("reasoning_tokens"))
            if isinstance(completion_details, dict)
            else None
        ),
    )


@dataclass(slots=True)
class OpenAICompatibleStreamParser:
    """同步和异步 stream 共用的确定性解析状态。"""

    model: str
    options: ProviderStreamOptions
    content_parts: list[str] = field(default_factory=list)
    thinking_parts: list[str] = field(default_factory=list)
    tool_builders: dict[int, dict[str, str]] = field(default_factory=dict)
    response_id: str = "stream"
    response_model: str | None = None
    stop_reason: str | None = None
    usage: ProviderUsage | None = None
    started: bool = False
    content_index: int = 0
    thinking_index: int = 0
    tool_index: int = 0

    def __post_init__(self) -> None:
        self.response_model = self.model

    def feed(self, chunk: dict[str, object]) -> tuple[ProviderStreamEvent, ...]:
        """消费一个 chunk 并返回对应标准 stream events。"""

        events: list[ProviderStreamEvent] = []
        self.response_id = str(chunk.get("id") or self.response_id)
        self.response_model = (
            self.model if chunk.get("model") is None else str(chunk.get("model"))
        )
        if not self.started:
            self.started = True
            events.append(
                ProviderStreamStarted(
                    request_id=self.response_id,
                    thinking_requested=self.options.thinking,
                    thinking_supported=True,
                ),
            )
        raw_usage = chunk.get("usage")
        if raw_usage is not None:
            self.usage = parse_openai_compatible_usage(raw_usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return tuple(events)
        choice = choices[0]
        if not isinstance(choice, dict):
            return tuple(events)
        raw_finish_reason = choice.get("finish_reason")
        if raw_finish_reason is not None:
            self.stop_reason = str(raw_finish_reason)
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return tuple(events)
        events.extend(self._content_events(delta))
        events.extend(self._tool_events(delta))
        return tuple(events)

    def finish(self) -> tuple[ProviderStreamEvent, ...]:
        """完成 stream 并生成最终 ProviderResponse。"""

        events: list[ProviderStreamEvent] = []
        if not self.started:
            events.append(
                ProviderStreamStarted(
                    request_id=self.response_id,
                    thinking_requested=self.options.thinking,
                    thinking_supported=False,
                ),
            )
        response = ProviderResponse(
            content="".join(self.content_parts),
            tool_calls=self._built_tool_calls(),
            stop_reason=self.stop_reason,
            usage=self.usage,
            model=self.response_model,
            provider_name="openai-compatible",
            response_id=self.response_id,
            thinking_content="".join(self.thinking_parts) or None,
        )
        events.append(
            ProviderStreamCompleted(
                request_id=self.response_id,
                response=response,
                stop_reason=self.stop_reason,
            ),
        )
        return tuple(events)

    def _content_events(
        self,
        delta: dict[str, object],
    ) -> tuple[ProviderStreamEvent, ...]:
        events: list[ProviderStreamEvent] = []
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            self.thinking_parts.append(reasoning)
            if self.options.thinking and self.options.show_thinking:
                self.thinking_index += 1
                text = reasoning
                if self.options.max_thinking_chars is not None:
                    text = text[: self.options.max_thinking_chars]
                events.append(
                    ProviderThinkingDelta(
                        request_id=self.response_id,
                        index=self.thinking_index,
                        text=text,
                    ),
                )
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.content_parts.append(content)
            self.content_index += 1
            events.append(
                ProviderContentDelta(
                    request_id=self.response_id,
                    index=self.content_index,
                    text=content,
                ),
            )
        return tuple(events)

    def _tool_events(
        self,
        delta: dict[str, object],
    ) -> tuple[ProviderStreamEvent, ...]:
        events: list[ProviderStreamEvent] = []
        for raw_tool_call in delta.get("tool_calls") or []:
            if not isinstance(raw_tool_call, dict):
                continue
            index = int(raw_tool_call.get("index", 0))
            builder = self.tool_builders.setdefault(
                index,
                {"id": "", "name": "", "arguments": ""},
            )
            tool_call_id, name_delta, arguments_delta = self._apply_tool_delta(
                builder,
                raw_tool_call,
                index,
            )
            self.tool_index += 1
            events.append(
                ProviderToolCallDelta(
                    request_id=self.response_id,
                    index=self.tool_index,
                    tool_call_id=tool_call_id,
                    name_delta=name_delta,
                    arguments_delta=arguments_delta,
                ),
            )
        return tuple(events)

    def _apply_tool_delta(
        self,
        builder: dict[str, str],
        raw_tool_call: dict[str, object],
        index: int,
    ) -> tuple[str | None, str | None, str | None]:
        tool_call_id = raw_tool_call.get("id")
        if not builder["id"]:
            builder["id"] = (
                tool_call_id
                if isinstance(tool_call_id, str) and tool_call_id
                else self._fallback_tool_call_id(index)
            )
        name_delta = None
        arguments_delta = None
        function = raw_tool_call.get("function")
        if isinstance(function, dict):
            raw_name = function.get("name")
            if isinstance(raw_name, str):
                builder["name"] += raw_name
                name_delta = raw_name
            raw_arguments = function.get("arguments")
            if isinstance(raw_arguments, str):
                builder["arguments"] += raw_arguments
                arguments_delta = raw_arguments
        return builder["id"] or None, name_delta, arguments_delta

    def _built_tool_calls(self) -> tuple[ProviderToolCall, ...]:
        tool_calls: list[ProviderToolCall] = []
        for index in sorted(self.tool_builders):
            item = self.tool_builders[index]
            tool_calls.append(
                ProviderToolCall(
                    id=item["id"] or self._fallback_tool_call_id(index),
                    name=require_tool_call_name(
                        item["name"],
                        provider_name="OpenAI-compatible",
                    ),
                    arguments=parse_json_object_arguments(
                        item["arguments"] or "{}",
                        provider_name="OpenAI-compatible",
                    ),
                ),
            )
        return tuple(tool_calls)

    def _fallback_tool_call_id(self, index: int) -> str:
        stream_digest = hashlib.sha256(
            self.response_id.encode("utf-8"),
        ).hexdigest()[:16]
        return f"call_fallback_{stream_digest}_{index}"


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
