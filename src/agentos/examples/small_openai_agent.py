import argparse
import os
import sys
from pathlib import Path

from agentos.builder import AgentBuilder
from agentos.capabilities import read_file_tool
from agentos.examples._small_openai_support import (
    provider_from_env,
    traced_provider,
)
from agentos.examples._stream_output import write_stream_event
from agentos.observability import (
    CapturePolicy,
    ObservabilityConfig,
    create_langfuse_otel_tracer,
    instrument_query_loop,
    use_observability_context,
)
from agentos.providers import Provider
from agentos.runtime import Agent, AgentResult, RunOptions
from agentos.sync import SyncAgent


def build_agent(
    provider: Provider,
    project_root: str | Path = ".",
    observability_config: ObservabilityConfig | None = None,
) -> Agent:
    """构建一个带 read_file 工具的小型 agent。"""

    agent = (
        AgentBuilder()
        .provider(provider)
        .tools([read_file_tool(root=project_root)])
        .build(session_id="session_small_openai_agent")
    )
    if observability_config is None:
        return agent
    return Agent(
        query_loop=instrument_query_loop(agent.query_loop, observability_config),
    )  # type: ignore[arg-type]


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
    agent = build_agent(
        provider=provider,
        project_root=Path.cwd(),
        observability_config=observability_config,
    )
    user_id = os.environ.get("AGENTOS_USER_ID")
    with use_observability_context(user_id=user_id or None):
        with SyncAgent(agent) as sync_agent:
            if args.stream:
                stream_options = RunOptions(
                    thinking=args.show_thinking,
                    show_thinking=args.show_thinking,
                )
                with sync_agent.run(
                    user_message,
                    stream=True,
                    options=stream_options,
                ) as stream:
                    for event in stream:
                        write_stream_event(
                            event,
                            output_format=args.output_format,
                            show_thinking=args.show_thinking,
                        )
            else:
                result = sync_agent.run(user_message)
                if not isinstance(result, AgentResult):
                    raise RuntimeError("small agent unexpectedly entered waiting state")
                print(result.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
