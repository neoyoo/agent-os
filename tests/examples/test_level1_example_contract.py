import ast
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = (
    PROJECT_ROOT / "src" / "agentos" / "examples" / "small_openai_agent.py",
    PROJECT_ROOT / "src" / "agentos" / "examples" / "context_protocol_agent.py",
)
FORBIDDEN_CONSTRUCTORS = frozenset(
    {
        "ContextRuntime",
        "MessageRuntime",
        "ProviderRequestBuilder",
        "QueryLoop",
    }
)


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.stem)
def test_level1_examples_are_builder_first(example: Path) -> None:
    tree = ast.parse(example.read_text(encoding="utf-8"), filename=str(example))
    called_names = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (name := _called_name(node)) is not None
    }
    imported_names = {
        alias.name.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "AgentBuilder" in called_names
    assert called_names.isdisjoint(FORBIDDEN_CONSTRUCTORS)
    assert imported_names.isdisjoint(FORBIDDEN_CONSTRUCTORS)
