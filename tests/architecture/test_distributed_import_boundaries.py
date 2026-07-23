import ast
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "agentos" / "distributed"
COMPOSITION_ROOTS = {"__init__.py", "profile.py", "_claim_artifacts.py"}
FORBIDDEN_ROOT_IMPORTS = {"agentos"}
FORBIDDEN_DOMAIN_IMPORTS = (
    "agentos.channels",
    "agentos.durable",
    "agentos.multi",
    "agentos.persistence",
    "agentos.providers",
    "agentos.artifacts.in_memory",
    "agentos.artifacts.sqlite_blobs",
    "agentos.artifacts.sqlite_filesystem",
    "agentos.artifacts.store",
)
FORBIDDEN_RUNTIME_MODULES = (
    "agentos.runtime.agent",
    "agentos.runtime.query_loop",
    "agentos.runtime.run_driver",
)
FORBIDDEN_RUNTIME_SYMBOLS = {
    "Agent",
    "Provider",
    "QueryLoop",
    "RunDriver",
    "Worker",
}


def test_distributed_contract_models_do_not_depend_on_legacy_owners() -> None:
    violations: dict[str, list[str]] = {}
    for path in _contract_paths():
        imported = _imports(path)
        forbidden = [
            module
            for module in imported
            if _is_forbidden_domain_import(module)
        ]
        if forbidden:
            violations[str(path.relative_to(PROJECT_ROOT))] = forbidden

    assert violations == {}


def test_distributed_contract_modules_do_not_import_execution_owners() -> None:
    violations: dict[str, list[str]] = {}
    for path in _contract_paths():
        imported = _imported_symbols(path)
        modules = _imports(path)
        forbidden = sorted(imported & FORBIDDEN_RUNTIME_SYMBOLS)
        forbidden.extend(
            module
            for module in modules
            if module == "agentos.runtime"
            or module.startswith(FORBIDDEN_RUNTIME_MODULES)
        )
        if forbidden:
            violations[str(path.relative_to(PROJECT_ROOT))] = forbidden

    assert violations == {}


def test_distributed_contract_imports_do_not_load_backend_clients() -> None:
    script = """
import json
import sys
sys.path.insert(0, "src")
import agentos.distributed.models
import agentos.distributed.protocols
import agentos.distributed.services
import agentos.distributed.errors
prefixes = ("aiosqlite", "asyncpg", "psycopg", "psycopg2", "redis", "sqlite3")
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


def test_distributed_profile_does_not_require_durable_sqlite_extra() -> None:
    script = """
import sys
sys.path.insert(0, "src")
sys.modules["aiosqlite"] = None
import agentos.distributed.profile
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_execution_owner_gate_cannot_be_bypassed_with_aliases(tmp_path: Path) -> None:
    symbol_alias = tmp_path / "symbol_alias.py"
    symbol_alias.write_text(
        "from agentos.runtime import Agent as RuntimeAgent\n",
        encoding="utf-8",
    )
    module_alias = tmp_path / "module_alias.py"
    module_alias.write_text(
        "import agentos.runtime.query_loop as loop_module\n",
        encoding="utf-8",
    )
    root_alias = tmp_path / "root_alias.py"
    root_alias.write_text("import agentos as sdk\n", encoding="utf-8")

    assert _imported_symbols(symbol_alias) & FORBIDDEN_RUNTIME_SYMBOLS == {"Agent"}
    assert _imports(module_alias) == ["agentos.runtime.query_loop"]
    assert _imports(root_alias) == ["agentos"]
    assert _is_forbidden_domain_import(_imports(root_alias)[0]) is True


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def _contract_paths() -> tuple[Path, ...]:
    return tuple(
        path
        for path in SOURCE_ROOT.glob("*.py")
        if path.name not in COMPOSITION_ROOTS
    )


def _is_forbidden_domain_import(module: str) -> bool:
    return module in FORBIDDEN_ROOT_IMPORTS or module.startswith(
        FORBIDDEN_DOMAIN_IMPORTS,
    )


def _imported_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            symbols.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            symbols.update(alias.name for alias in node.names)
    return symbols
