import json
import os
from collections.abc import Iterator
from pathlib import Path

from agentos.providers import (
    OpenAICompatibleProvider,
    Provider,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderToolSpec,
    complete_response_to_stream_events,
    provider_tool_spec_to_dict,
)
from agentos.providers.input import ProviderInputItem
from agentos.providers.input_serialization import provider_input_to_dict


def load_dotenv(env_file: str | Path = ".env") -> None:
    """加载本地 .env 文件，但不覆盖已经存在的环境变量。"""

    path = Path(env_file)
    if not path.is_file():
        return

    for line in path.read_text().splitlines():
        key_value = _parse_env_line(line)
        if key_value is None:
            continue
        key, value = key_value
        os.environ.setdefault(key, value)


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """解析一行 .env 内容。"""

    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").strip()
    if "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def provider_from_env(env_file: str | Path = ".env") -> OpenAICompatibleProvider:
    """从环境变量创建 OpenAI-compatible provider。"""

    explicit_env = {
        key
        for key in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_MODEL",
        )
        if os.environ.get(key)
    }
    load_dotenv(env_file)
    provider_prefix = _provider_prefix(explicit_env)
    api_key = _provider_env_value(provider_prefix, "API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY or DEEPSEEK_API_KEY is required")
    base_url = (
        _provider_env_value(provider_prefix, "BASE_URL")
        or "https://api.deepseek.com"
    )
    model = _provider_env_value(provider_prefix, "MODEL") or "deepseek-chat"
    thinking = _thinking_from_env(base_url)
    _ensure_non_thinking_model(model, thinking)

    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=float(os.environ.get("OPENAI_TIMEOUT", "60")),
        thinking=thinking,
    )


def _provider_prefix(explicit_env: set[str]) -> str:
    """根据显式环境变量和 .env 结果选择 provider 配置前缀。"""

    if "OPENAI_API_KEY" in explicit_env:
        return "OPENAI"
    if "DEEPSEEK_API_KEY" in explicit_env:
        return "DEEPSEEK"
    if os.environ.get("OPENAI_API_KEY"):
        return "OPENAI"
    return "DEEPSEEK"


def _provider_env_value(prefix: str, suffix: str) -> str | None:
    """读取同组 provider 配置，缺失时回退到另一组。"""

    primary = f"{prefix}_{suffix}"
    fallback_prefix = "DEEPSEEK" if prefix == "OPENAI" else "OPENAI"
    fallback = f"{fallback_prefix}_{suffix}"
    return os.environ.get(primary) or os.environ.get(fallback)


def _thinking_from_env(base_url: str) -> dict[str, object] | None:
    """读取 thinking 配置；DeepSeek 默认关闭 thinking。"""

    raw = os.environ.get("OPENAI_THINKING") or os.environ.get("DEEPSEEK_THINKING")
    if raw is None and "deepseek" in base_url:
        raw = "disabled"
    if raw is None:
        return None

    value = raw.strip().lower()
    if value in {"", "omit", "none"}:
        return None
    if value in {"disabled", "disable", "off", "false", "0"}:
        return {"type": "disabled"}
    if value in {"enabled", "enable", "on", "true", "1"}:
        return {"type": "enabled"}
    raise RuntimeError(
        "OPENAI_THINKING/DEEPSEEK_THINKING must be disabled, enabled, or omit",
    )


def _ensure_non_thinking_model(
    model: str,
    thinking: dict[str, object] | None,
) -> None:
    """避免用强 thinking 模型搭配 disabled thinking。"""

    if model == "deepseek-reasoner" and thinking == {"type": "disabled"}:
        raise RuntimeError(
            "deepseek-reasoner is a thinking model. "
            "Use deepseek-chat when thinking is disabled.",
        )


class TracedProvider:
    """在 provider 边界打印完整 LLM request/response 的调试包装器。"""

    def __init__(self, provider: Provider) -> None:
        self._provider = provider
        self._request_count = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """打印请求、调用真实 provider、再打印标准化响应。"""

        self._request_count += 1
        self._print_request(self._request_count, request)
        response = self._provider.complete(request)
        self._print_response(self._request_count, response)
        return response

    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """打印请求，并透传 provider streaming events。"""

        self._request_count += 1
        request_number = self._request_count
        self._print_request(request_number, request)
        response: ProviderResponse | None = None
        stream = getattr(self._provider, "stream", None)
        if callable(stream):
            events = stream(request, options)
        else:
            events = complete_response_to_stream_events(
                request_id=f"trace_provider_{request_number}",
                response=self._provider.complete(request),
                options=options,
            )
        for event in events:
            if isinstance(event, ProviderStreamCompleted):
                response = event.response
            yield event
        if response is not None:
            self._print_response(request_number, response)

    def _print_request(self, number: int, request: ProviderRequest) -> None:
        print(f"=== LLM Request #{number} ===")
        print("--- system ---")
        print(request.system)
        print("--- messages ---")
        print(_json_dumps(request.messages))
        print("--- tools ---")
        print(_json_dumps(request.tools))

    def _print_response(self, number: int, response: ProviderResponse) -> None:
        print(f"=== LLM Response #{number} ===")
        print(
            _json_dumps(
                {
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                        }
                        for tool_call in response.tool_calls
                    ],
                },
            ),
        )


def traced_provider(provider: Provider) -> TracedProvider:
    """创建带 LLM 上下文 trace 输出的 provider。"""

    return TracedProvider(provider)


def _json_dumps(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, ProviderToolSpec):
        return provider_tool_spec_to_dict(value)
    if isinstance(value, ProviderInputItem):
        return provider_input_to_dict(value)
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable",
    )
