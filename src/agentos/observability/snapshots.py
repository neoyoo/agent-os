from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from agentos.capabilities import ToolExecutionResult
from agentos.observability.config import CapturePolicy, default_redactor
from agentos.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)


@dataclass(frozen=True, slots=True)
class ProviderRequestSnapshot:
    """ProviderRequest 的 policy-aware 观测快照。"""

    system: str | None
    messages: tuple[dict[str, object], ...] | None
    tools: tuple[dict[str, object], ...] | None
    system_length: int
    message_count: int
    tool_count: int
    system_sha256: str
    messages_sha256: str
    tools_sha256: str


@dataclass(frozen=True, slots=True)
class ToolCallSnapshot:
    """provider tool call 的观测快照。"""

    id: str
    name: str
    arguments: dict[str, object] | None
    arguments_sha256: str


@dataclass(frozen=True, slots=True)
class ProviderResponseSnapshot:
    """ProviderResponse 的 policy-aware 观测快照。"""

    content: str | None
    content_length: int
    content_sha256: str
    thinking_content: str | None
    thinking_length: int
    thinking_sha256: str
    tool_calls: tuple[ToolCallSnapshot, ...]
    stop_reason: str | None
    usage: ProviderUsage | None
    model: str | None
    provider_name: str | None
    response_id: str | None


@dataclass(frozen=True, slots=True)
class ToolResultSnapshot:
    """tool result 的观测快照。"""

    tool_call_id: str
    content: str | None
    content_length: int
    content_sha256: str


def stable_sha256(value: object) -> str:
    """对任意 JSON-safe 值生成稳定 SHA-256。"""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_provider_request_snapshot(
    request: ProviderRequest,
    policy: CapturePolicy,
) -> ProviderRequestSnapshot:
    """基于 capture policy 构造 ProviderRequestSnapshot。"""

    return ProviderRequestSnapshot(
        system=(
            _captured_string(request.system, policy)
            if policy.capture_system
            else None
        ),
        messages=(
            _captured_dicts(request.messages, policy)
            if policy.capture_messages
            else None
        ),
        tools=(
            _captured_dicts(request.tools, policy)
            if policy.capture_tool_schemas
            else None
        ),
        system_length=len(request.system),
        message_count=len(request.messages),
        tool_count=len(request.tools),
        system_sha256=stable_sha256(request.system),
        messages_sha256=stable_sha256(request.messages),
        tools_sha256=stable_sha256(request.tools),
    )


def build_provider_response_snapshot(
    response: ProviderResponse,
    policy: CapturePolicy,
) -> ProviderResponseSnapshot:
    """基于 capture policy 构造 ProviderResponseSnapshot。"""

    thinking_content = response.thinking_content or ""
    return ProviderResponseSnapshot(
        content=(
            _captured_string(response.content, policy)
            if policy.capture_provider_output
            else None
        ),
        content_length=len(response.content),
        content_sha256=stable_sha256(response.content),
        thinking_content=(
            _captured_string(thinking_content, policy)
            if policy.capture_thinking and thinking_content
            else None
        ),
        thinking_length=len(thinking_content),
        thinking_sha256=stable_sha256(thinking_content),
        tool_calls=tuple(
            build_tool_call_snapshot(tool_call, policy)
            for tool_call in response.tool_calls
        ),
        stop_reason=response.stop_reason,
        usage=response.usage,
        model=response.model,
        provider_name=response.provider_name,
        response_id=response.response_id,
    )


def build_tool_call_snapshot(
    tool_call: ProviderToolCall,
    policy: CapturePolicy,
) -> ToolCallSnapshot:
    """基于 capture policy 构造 ToolCallSnapshot。"""

    return ToolCallSnapshot(
        id=tool_call.id,
        name=tool_call.name,
        arguments=(
            _captured_dict(tool_call.arguments, policy)
            if policy.capture_tool_arguments
            else None
        ),
        arguments_sha256=stable_sha256(tool_call.arguments),
    )


def build_tool_result_snapshot(
    result: ToolExecutionResult,
    policy: CapturePolicy,
) -> ToolResultSnapshot:
    """基于 capture policy 构造 ToolResultSnapshot。"""

    return ToolResultSnapshot(
        tool_call_id=result.tool_call_id,
        content=(
            _captured_string(result.content, policy)
            if policy.capture_tool_result
            else None
        ),
        content_length=len(result.content),
        content_sha256=stable_sha256(result.content),
    )


def _captured_string(value: str, policy: CapturePolicy) -> str:
    """按 policy 捕获字符串。"""

    captured = value if policy.mode == "full" else str(default_redactor(value))
    if len(captured) > policy.max_string_length:
        return captured[: policy.max_string_length] + "..."
    return captured


def _captured_dicts(
    values: list[dict[str, object]],
    policy: CapturePolicy,
) -> tuple[dict[str, object], ...]:
    """按 policy 捕获 dict list。"""

    return tuple(_captured_dict(value, policy) for value in values)


def _captured_dict(
    value: dict[str, object],
    policy: CapturePolicy,
) -> dict[str, object]:
    """按 policy 捕获 dict。"""

    captured = value if policy.mode == "full" else default_redactor(value)
    if not isinstance(captured, dict):
        return {}
    return _limit_dict_strings(captured, policy.max_string_length)


def _limit_dict_strings(
    value: dict[object, object],
    max_length: int,
) -> dict[str, object]:
    """限制 dict 中字符串长度，并把 key 标准化为 str。"""

    result: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(item, str) and len(item) > max_length:
            result[str(key)] = item[:max_length] + "..."
        elif isinstance(item, dict):
            result[str(key)] = _limit_dict_strings(item, max_length)
        elif isinstance(item, list):
            result[str(key)] = [
                _limit_dict_strings(element, max_length)
                if isinstance(element, dict)
                else element
                for element in item
            ]
        else:
            result[str(key)] = item
    return result
