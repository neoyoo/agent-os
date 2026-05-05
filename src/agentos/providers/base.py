from dataclasses import dataclass, field
from typing import Any, Protocol


ProviderMessage = dict[str, object]
ProviderToolSpec = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderToolCall:
    """provider 返回的标准化工具调用。"""

    id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """发送给 provider 的标准化请求。"""

    system: str
    messages: list[ProviderMessage]
    tools: list[ProviderToolSpec] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """provider 返回的标准化 token/cost usage。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """provider 返回的标准化响应。"""

    content: str = ""
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: ProviderUsage | None = None
    model: str | None = None
    provider_name: str | None = None
    response_id: str | None = None
    thinking_content: str | None = None


class Provider(Protocol):
    """provider runtime 的最小协议。"""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """根据标准请求返回 assistant 响应。"""
