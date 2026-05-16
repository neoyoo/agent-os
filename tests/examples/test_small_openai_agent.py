from pathlib import Path

from agentos.examples.small_openai_agent import (
    build_agent,
    main,
    provider_from_env,
    traced_provider,
)
from agentos.observability import CapturePolicy, InMemoryTracer, ObservabilityConfig
from agentos.observability.instrumented import InstrumentedQueryLoop
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall


def test_build_agent_wires_read_file_tool_for_small_agent() -> None:
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_read",
                        name="read_file",
                        arguments={"path": "pyproject.toml"},
                    ),
                ],
            ),
            ProviderResponse(content="项目名是 agent-os。"),
        ],
    )
    loop = build_agent(provider=provider, project_root=Path.cwd())

    answer = loop.run_turn("读取 pyproject.toml 里的项目名")

    assert answer == "项目名是 agent-os。"
    tool_names = [tool["function"]["name"] for tool in provider.requests[0].tools]
    assert tool_names[:5] == [
        "declare_schema",
        "update_state",
        "extend_schema",
        "start_chapter",
        "recall_context",
    ]
    assert "read_file" in tool_names
    assert provider.requests[1].messages[-1]["role"] == "tool"
    assert 'name = "agent-os"' in str(provider.requests[1].messages[-1]["content"])


def test_build_agent_renders_capability_plane_from_registered_tools() -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    loop = build_agent(provider=provider, project_root=Path.cwd())

    loop.run_turn("hello")

    system = provider.requests[0].system
    assert "- Registered tools: `read_file` — 读取项目内文本文件内容。" in system
    assert "edit_file" not in system
    assert "run_shell" not in system
    tool_names = [tool["function"]["name"] for tool in provider.requests[0].tools]
    assert "recall_context" in tool_names
    assert "read_file" in tool_names



def test_build_agent_can_enable_observability(tmp_path) -> None:
    tracer = InMemoryTracer()
    provider = FakeProvider([ProviderResponse(content="ok")])

    loop = build_agent(
        provider=provider,
        project_root=tmp_path,
        observability_config=ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    assert isinstance(loop, InstrumentedQueryLoop)
    assert loop.run_turn("hello") == "ok"
    assert [record.name for record in tracer.records] == [
        "agent.turn",
        "provider.request.build",
        "provider.stream",
    ]


def test_provider_from_env_uses_openai_compatible_settings(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.example")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-chat")

    provider = provider_from_env()

    assert provider.api_key == "test-key"
    assert provider.base_url == "https://api.deepseek.example"
    assert provider.model == "deepseek-chat"


def test_provider_from_env_accepts_deepseek_env_aliases(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.example")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    provider = provider_from_env()

    assert provider.api_key == "deepseek-key"
    assert provider.base_url == "https://api.deepseek.example"
    assert provider.model == "deepseek-chat"


def test_provider_from_env_loads_dotenv_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'DEEPSEEK_API_KEY="dotenv-key"',
                "DEEPSEEK_BASE_URL=https://api.deepseek.example",
                "DEEPSEEK_MODEL=deepseek-chat",
            ],
        ),
    )

    provider = provider_from_env(env_file=env_file)

    assert provider.api_key == "dotenv-key"
    assert provider.base_url == "https://api.deepseek.example"
    assert provider.model == "deepseek-chat"
    assert provider.thinking == {"type": "disabled"}


def test_provider_from_env_rejects_reasoner_when_thinking_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("OPENAI_THINKING", "disabled")

    try:
        provider_from_env()
    except RuntimeError as error:
        assert "deepseek-reasoner" in str(error)
        assert "deepseek-chat" in str(error)
    else:
        raise AssertionError("Expected RuntimeError")


def test_traced_provider_prints_full_llm_request_and_response(capsys) -> None:
    provider = traced_provider(FakeProvider([ProviderResponse(content="ok")]))

    response = provider.complete(
        type(
            "Request",
            (),
            {
                "system": "# Runtime Contract\nsystem text",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [{"type": "function", "function": {"name": "read_file"}}],
            },
        )(),
    )

    output = capsys.readouterr().out
    assert response.content == "ok"
    assert "=== LLM Request #1 ===" in output
    assert "--- system ---" in output
    assert "# Runtime Contract" in output
    assert "--- messages ---" in output
    assert '"role": "user"' in output
    assert "--- tools ---" in output
    assert '"name": "read_file"' in output
    assert "=== LLM Response #1 ===" in output
    assert '"content": "ok"' in output


def test_main_accepts_trace_flag(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )

    exit_code = main(["--trace", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "=== LLM Request #1 ===" in output
    assert "--- system ---" in output
    assert "--- messages ---" in output
    assert "=== LLM Response #1 ===" in output
    assert "ok" in output


def test_main_accepts_observe_langfuse_flag(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    tracer = InMemoryTracer()
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("AGENTOS_OBSERVABILITY_CAPTURE", "metadata")
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.create_langfuse_otel_tracer",
        lambda **kwargs: tracer,
    )

    exit_code = main(["--observe-langfuse", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "ok" in output
    assert [record.name for record in tracer.records] == [
        "agent.turn",
        "provider.request.build",
        "provider.stream",
    ]


def test_main_observe_langfuse_uses_agentos_user_id(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    tracer = InMemoryTracer()
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("AGENTOS_OBSERVABILITY_CAPTURE", "metadata")
    monkeypatch.setenv("AGENTOS_USER_ID", "local_user")
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.create_langfuse_otel_tracer",
        lambda **kwargs: tracer,
    )

    exit_code = main(["--observe-langfuse", "hello"])

    assert exit_code == 0
    capsys.readouterr()
    for record in tracer.records:
        assert record.attributes["langfuse.user.id"] == "local_user"
        assert record.attributes["user.id"] == "local_user"
        assert "agentos.user.id" not in record.attributes


def test_main_accepts_stream_flag(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )

    exit_code = main(["--stream", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "ok" in output


def test_main_accepts_stream_json_output(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )

    exit_code = main(["--stream", "--output-format", "stream-json", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"type":"content_delta"' in output
    assert '"type":"done"' in output


def test_main_accepts_sse_output(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )

    exit_code = main(["--stream", "--output-format", "sse", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "event: content_delta" in output
    assert "event: done" in output
