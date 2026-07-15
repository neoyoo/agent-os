from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = PROJECT_ROOT / "src" / "agentos" / "providers"


def _line_count(name: str) -> int:
    return len((PROVIDERS / name).read_text(encoding="utf-8").splitlines())


def test_openai_compatible_modules_have_single_responsibilities() -> None:
    assert _line_count("openai_compatible.py") < 300
    for name in (
        "openai_chat_wire.py",
        "openai_compatible_wire.py",
        "openai_compatible_parsing.py",
        "openai_compatible_transport.py",
    ):
        assert _line_count(name) < 500


def test_openai_chat_wire_is_the_only_chat_primitive_owner() -> None:
    assert not (PROVIDERS / "_openai_compatible_payload.py").exists()
    assert not (PROVIDERS / "_content_parts.py").exists()
    compatible_wire = (PROVIDERS / "openai_compatible_wire.py").read_text(
        encoding="utf-8",
    )
    chat_wire = (PROVIDERS / "openai_chat_wire.py").read_text(encoding="utf-8")
    assert "from agentos.providers.openai_chat_wire import" in compatible_wire
    assert "openai_compatible" not in chat_wire


def test_sync_and_async_stream_use_the_same_parser_state() -> None:
    facade = (PROVIDERS / "openai_compatible.py").read_text(encoding="utf-8")
    assert facade.count("OpenAICompatibleStreamParser(") == 2


def test_openai_compatible_facade_has_no_wire_or_fallback_identity_state() -> None:
    facade = (PROVIDERS / "openai_compatible.py").read_text(encoding="utf-8")

    assert "def _message(" not in facade
    assert "time_ns" not in facade
    assert "_fallback_tool_call_ids" not in facade
