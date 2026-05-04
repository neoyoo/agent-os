from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "langfuse_smoke_test.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("langfuse_smoke_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_dotenv_sets_missing_values_without_overriding_existing(monkeypatch, tmp_path):
    script = _load_script_module()
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "LANGFUSE_PUBLIC_KEY=pk-from-file",
                "LANGFUSE_SECRET_KEY='sk-from-file'",
                'OPENAI_MODEL="deepseek-v4-pro"',
                "export OPENAI_BASE_URL=https://api.deepseek.com/v1",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-existing")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    assert script._load_dotenv(dotenv_path) is True
    assert script.os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-existing"
    assert script.os.environ["LANGFUSE_SECRET_KEY"] == "sk-from-file"
    assert script.os.environ["OPENAI_MODEL"] == "deepseek-v4-pro"
    assert script.os.environ["OPENAI_BASE_URL"] == "https://api.deepseek.com/v1"


def test_configure_langfuse_base_url_accepts_langfuse_host(monkeypatch):
    script = _load_script_module()

    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    assert script._configure_langfuse_base_url() == "http://localhost:3000"
    assert script.os.environ["LANGFUSE_BASE_URL"] == "http://localhost:3000"
