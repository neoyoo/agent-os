from pathlib import Path

from agentos.context import ContextRenderer, ContextState


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_context_and_provider_layers_do_not_import_middleware_clients() -> None:
    forbidden = ("redis", "qdrant", "psycopg", "postgres")
    checked_roots = [
        PROJECT_ROOT / "src" / "agentos" / "runtime",
        PROJECT_ROOT / "src" / "agentos" / "context",
        PROJECT_ROOT / "src" / "agentos" / "messages",
        PROJECT_ROOT / "src" / "agentos" / "providers",
        PROJECT_ROOT / "src" / "agentos" / "capabilities",
    ]

    matches: list[str] = []
    for root in checked_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for term in forbidden:
                if term in text:
                    matches.append(f"{path.relative_to(PROJECT_ROOT)}:{term}")

    assert matches == []


def test_context_renderer_does_not_import_memory_package() -> None:
    renderer_source = (
        PROJECT_ROOT / "src" / "agentos" / "context" / "renderer.py"
    ).read_text(encoding="utf-8")

    assert "agentos.memory" not in renderer_source


def test_default_prompt_does_not_expose_memory_storage_metadata() -> None:
    rendered = ContextRenderer().render(ContextState())

    for forbidden in [
        "message_id",
        "session_id",
        "source_refs",
        "qdrant",
        "redis",
        "postgres",
        "embedding score",
    ]:
        assert forbidden not in rendered.lower()
