"""向本地 Langfuse 写入一条最小 smoke test trace。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV_PATH = REPO_ROOT / ".env"
DEFAULT_LANGFUSE_BASE_URL = "http://localhost:3000"
DEFAULT_OPENAI_MODEL = "deepseek-v4-pro"


def _load_dotenv(path: Path = DEFAULT_DOTENV_PATH) -> bool:
    if not path.exists():
        return False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if key and not os.environ.get(key):
            os.environ[key] = value

    return True


def _configure_langfuse_base_url() -> str:
    base_url = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
    if not base_url:
        base_url = DEFAULT_LANGFUSE_BASE_URL

    os.environ["LANGFUSE_BASE_URL"] = base_url
    return base_url.rstrip("/")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value

    print(f"Missing required environment variable: {name}", file=sys.stderr)
    print("Set it in .env or export it in your shell.", file=sys.stderr)
    sys.exit(2)


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    return None


def _usage_details(completion) -> dict[str, int]:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return {}

    details: dict[str, int] = {}
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    if prompt_tokens is not None:
        details["input_tokens"] = prompt_tokens
    if completion_tokens is not None:
        details["output_tokens"] = completion_tokens
    if total_tokens is not None:
        details["total_tokens"] = total_tokens
    return details


def _call_openai_compatible_llm(prompt: str) -> tuple[str, dict[str, int]]:
    api_key = _require_env("OPENAI_API_KEY")
    model = _env_value("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    base_url = _env_value("OPENAI_BASE_URL")

    try:
        from langfuse.openai import OpenAI
    except ImportError:
        print(
            "Missing package for OpenAI-compatible calls.\n"
            "Run with: uv run --with langfuse --with openai python scripts/langfuse_smoke_test.py",
            file=sys.stderr,
        )
        raise

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    completion = client.chat.completions.create(
        name="deepseek-smoke-generation",
        model=model,
        messages=[
            {"role": "system", "content": "你是一个简洁准确的中文助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=256,
        metadata={
            "provider": "openai-compatible",
            "base_url": base_url or "openai-default",
        },
    )

    content = completion.choices[0].message.content or ""
    return content, _usage_details(completion)


def _write_fake_generation(root_span, prompt: str) -> str:
    output = "Langfuse 是 LLM 应用的观测、调试和评估平台。"
    with root_span.start_as_current_observation(
        as_type="generation",
        name="fake-llm-generation",
        model="demo-model",
        input={
            "messages": [
                {"role": "user", "content": prompt},
            ]
        },
    ) as generation:
        generation.update(
            output={"role": "assistant", "content": output},
            usage_details={"input_tokens": 12, "output_tokens": 18},
        )
    return output


def main() -> int:
    dotenv_loaded = _load_dotenv()
    base_url = _configure_langfuse_base_url()

    _require_env("LANGFUSE_PUBLIC_KEY")
    _require_env("LANGFUSE_SECRET_KEY")

    try:
        from langfuse import get_client, propagate_attributes
    except ImportError:
        print(
            "Missing package: langfuse\n"
            "Run with: uv run --with langfuse python scripts/langfuse_smoke_test.py\n"
            "Or install it with: pip install langfuse",
            file=sys.stderr,
        )
        return 2

    session_id = os.environ.get("LANGFUSE_TEST_SESSION_ID", f"local-session-{uuid4().hex[:8]}")
    user_id = os.environ.get("LANGFUSE_TEST_USER_ID", "local-user")
    prompt = os.environ.get("LANGFUSE_TEST_PROMPT", "用一句中文解释 Langfuse 是什么。")
    has_openai_key = bool(_env_value("OPENAI_API_KEY"))

    langfuse = get_client()
    trace_id = ""
    llm_mode = "openai-compatible" if has_openai_key else "fake"
    llm_output = ""

    with langfuse.start_as_current_observation(
        as_type="span",
        name="local-smoke-test",
        input={"message": prompt},
    ) as root_span:
        trace_id = langfuse.get_current_trace_id() or ""

        with propagate_attributes(
            trace_name="local-smoke-test",
            user_id=user_id,
            session_id=session_id,
            tags=["local", "smoke-test"],
            metadata={"script": "scripts/langfuse_smoke_test.py"},
        ):
            with root_span.start_as_current_observation(
                as_type="span",
                name="fake-retrieval",
                input={"query": "Langfuse 是什么？"},
            ) as retrieval_span:
                retrieval_span.update(
                    output={
                        "documents": [
                            "Langfuse 用来观测、调试和评估 LLM 应用。",
                        ]
                    }
                )

            if has_openai_key:
                llm_output, _ = _call_openai_compatible_llm(prompt)
            else:
                llm_output = _write_fake_generation(root_span, prompt)

        root_span.update(
            output={
                "status": "ok",
                "llm_mode": llm_mode,
                "llm_output": llm_output,
                "session_id": session_id,
                "user_id": user_id,
            }
        )

    langfuse.flush()

    print("Sent Langfuse smoke test trace.")
    print(f"Loaded .env: {dotenv_loaded}")
    print(f"Base URL: {base_url}")
    print(f"Trace ID: {trace_id}")
    print(f"Session ID: {session_id}")
    print(f"LLM mode: {llm_mode}")
    if not has_openai_key:
        print("OPENAI_API_KEY is empty, so the script used a fake generation.")
    print("Open Langfuse UI -> Traces and search for the trace name: local-smoke-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
