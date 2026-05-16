import argparse
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry, read_file_tool
from agentos.context import CapabilityPlane, ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.observability import (
    CapturePolicy,
    ObservabilityConfig,
    create_langfuse_otel_tracer,
    instrument_query_loop,
    use_observability_context,
)
from agentos.providers import (
    OpenAICompatibleProvider,
    ProviderStreamEvent,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    ProviderToolSpec,
    ProviderRequest,
    ProviderResponse,
    Provider,
    complete_response_to_stream_events,
    provider_message_to_dict,
    provider_tool_spec_to_dict,
)
from agentos.runtime import (
    AssistantContentDelta,
    EventBus,
    ProviderRequestBuilder,
    QueryLoop,
    RunOptions,
    SessionState,
    TurnStreamCompleted,
    event_to_json,
    event_to_sse,
)


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
        timeout=float(os.environ.get("OPENAI_TIMEOUT", "60")),
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
        """保存被包装的 provider。"""

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
        """打印一次 provider request，不包含 API key 或 HTTP header。"""

        print(f"=== LLM Request #{number} ===")
        print("--- system ---")
        print(request.system)
        print("--- messages ---")
        print(_json_dumps(request.messages))
        print("--- tools ---")
        print(_json_dumps(request.tools))

    def _print_response(self, number: int, response: ProviderResponse) -> None:
        """打印一次标准化 provider response。"""

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
    """用稳定格式输出 JSON，便于人工阅读和测试断言。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    """把强类型 provider 边界对象转换为 JSON-safe dict。"""

    if isinstance(value, ProviderToolSpec):
        return provider_tool_spec_to_dict(value)
    try:
        return provider_message_to_dict(value)  # type: ignore[arg-type]
    except TypeError:
        raise TypeError(
            f"Object of type {type(value).__name__} is not JSON serializable",
        )


def build_agent(
    provider: Provider,
    project_root: str | Path = ".",
    observability_config: ObservabilityConfig | None = None,
) -> QueryLoop:
    """构建一个带 read_file 工具的小型 agent。"""

    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    registry.register(read_file_tool(root=project_root))
    capabilities = ToolCallRouter(
        tool_registry=registry,
        context_runtime=context,
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(
                capability_plane=CapabilityPlane(
                    tool_groups=[
                        registry.capability_tool_group("Registered tools"),
                    ],
                ),
            ),
            message_runtime=messages,
            tools=capabilities.tool_specs(),
        ),
        provider=provider,
        tool_call_router=capabilities,
        event_bus=EventBus(),
        session_state=SessionState(id="small_openai_agent"),
    )
    if observability_config is not None:
        return instrument_query_loop(loop, observability_config)  # type: ignore[return-value]
    return loop


def observability_config_from_env() -> ObservabilityConfig:
    """从环境变量创建 Langfuse OTel observability config。"""

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required "
            "when --observe-langfuse is used",
        )
    host = (
        os.environ.get("LANGFUSE_HOST")
        or os.environ.get("LANGFUSE_BASE_URL")
        or "http://localhost:3000"
    )
    capture_policy = _capture_policy_from_env()
    tracer = create_langfuse_otel_tracer(
        host=host,
        public_key=public_key,
        secret_key=secret_key,
        service_name="agentos-small-openai-agent",
        environment=os.environ.get("AGENTOS_ENVIRONMENT", "local"),
    )
    return ObservabilityConfig(
        tracer=tracer,
        capture_policy=capture_policy,
    )


def _capture_policy_from_env() -> CapturePolicy:
    """读取 AGENTOS_OBSERVABILITY_CAPTURE。"""

    mode = os.environ.get("AGENTOS_OBSERVABILITY_CAPTURE", "metadata").strip().lower()
    if mode == "metadata":
        return CapturePolicy.metadata_only()
    if mode == "redacted":
        return CapturePolicy.redacted()
    if mode == "full":
        return CapturePolicy.full_for_local_development()
    raise RuntimeError(
        "AGENTOS_OBSERVABILITY_CAPTURE must be metadata, redacted, or full",
    )


def main(argv: list[str] | None = None) -> int:
    """运行小型 OpenAI-compatible agent。"""

    parser = argparse.ArgumentParser(prog="agent-os-small-agent")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--observe-langfuse", action="store_true")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream typed output events.",
    )
    parser.add_argument(
        "--show-thinking",
        action="store_true",
        help="Show provider thinking/reasoning deltas when available.",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "stream-json", "sse"],
        default="text",
        help="Streaming output format.",
    )
    parser.add_argument("prompt", nargs="*")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    user_message = (
        " ".join(args.prompt)
        if args.prompt
        else "读取 pyproject.toml 里的项目名，并用一句话回答。"
    )
    provider: Provider = provider_from_env()
    if args.trace:
        provider = traced_provider(provider)
    observability_config = (
        observability_config_from_env()
        if args.observe_langfuse
        else None
    )
    loop = build_agent(
        provider=provider,
        project_root=Path.cwd(),
        observability_config=observability_config,
    )
    user_id = os.environ.get("AGENTOS_USER_ID")
    with use_observability_context(user_id=user_id or None):
        if args.stream:
            stream_options = RunOptions(
                thinking=args.show_thinking,
                show_thinking=args.show_thinking,
            )
            for event in loop.run_turn_stream(user_message, stream_options):
                if args.output_format == "text":
                    if isinstance(event, AssistantContentDelta):
                        print(event.text, end="", flush=True)
                    elif isinstance(event, TurnStreamCompleted):
                        print()
                elif args.output_format == "stream-json":
                    payload = event_to_json(
                        event,
                        show_thinking=args.show_thinking,
                    )
                    if payload is not None:
                        print(payload)
                elif args.output_format == "sse":
                    chunk = event_to_sse(
                        event,
                        show_thinking=args.show_thinking,
                    )
                    if chunk is not None:
                        print(chunk, end="")
        else:
            print(loop.run_turn(user_message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
