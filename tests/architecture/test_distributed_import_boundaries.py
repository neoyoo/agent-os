import ast
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "agentos" / "distributed"
FORBIDDEN_DOMAIN_IMPORTS = (
    "agentos.channels",
    "agentos.multi",
    "agentos.persistence",
)


def test_distributed_contract_models_do_not_depend_on_legacy_owners() -> None:
    violations: dict[str, list[str]] = {}
    for path in SOURCE_ROOT.glob("*.py"):
        imported = _imports(path)
        forbidden = [
            module
            for module in imported
            if module.startswith(FORBIDDEN_DOMAIN_IMPORTS)
        ]
        if forbidden:
            violations[str(path.relative_to(PROJECT_ROOT))] = forbidden

    assert violations == {}


def test_distributed_model_import_does_not_load_backend_clients() -> None:
    script = """
import json
import sys
sys.path.insert(0, "src")
import agentos.distributed.models
prefixes = ("asyncpg", "psycopg", "psycopg2", "redis")
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)
)
print(json.dumps(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules
