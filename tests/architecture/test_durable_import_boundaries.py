import ast
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
FORBIDDEN_PREFIXES = (
    "asyncpg",
    "psycopg",
    "psycopg2",
    "redis",
    "agentos.multi.postgres",
    "agentos.persistence.postgres",
    "agentos.persistence.redis",
)
DURABLE_BOUNDARY_FILES = (
    *sorted((SOURCE_ROOT / "agentos" / "durable").glob("*.py")),
    SOURCE_ROOT / "agentos" / "durable" / "profile.py",
    SOURCE_ROOT / "agentos" / "artifacts" / "sqlite_filesystem.py",
    SOURCE_ROOT / "agentos" / "planning" / "sqlite.py",
    SOURCE_ROOT / "agentos" / "memory" / "sqlite.py",
    SOURCE_ROOT / "agentos" / "capabilities" / "skill_activation.py",
)


def _imports(path: Path) -> tuple[str, ...]:
    modules = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(modules)


def _forbidden(modules: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        module
        for module in modules
        if any(
            module == prefix or module.startswith(prefix + ".")
            for prefix in FORBIDDEN_PREFIXES
        )
    )


def test_durable_source_boundary_has_no_distributed_client_imports() -> None:
    violations = {
        str(path.relative_to(PROJECT_ROOT)): _forbidden(_imports(path))
        for path in DURABLE_BOUNDARY_FILES
        if _forbidden(_imports(path))
    }

    assert violations == {}


def test_durable_public_imports_do_not_load_distributed_clients() -> None:
    script = """
import json
import sys
sys.path.insert(0, "src")
from agentos.artifacts import SqliteFilesystemArtifactStore
from agentos.capabilities import SQLiteSkillActivationStore
from agentos.durable import SQLiteDurableStore
from agentos.memory import SQLiteMemoryStore
from agentos.planning import SQLitePlanStore
from agentos.durable import DurableRuntimeProfile
from agentos.runtime import DurableRunCommand
prefixes = (
    "asyncpg", "psycopg", "psycopg2", "redis",
    "agentos.multi.postgres", "agentos.persistence.postgres",
    "agentos.persistence.redis",
)
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


def test_kernel_import_does_not_load_durable_adapters() -> None:
    script = """
import json
import sys
sys.path.insert(0, "src")
import agentos.runtime
adapters = (
    "agentos.artifacts.sqlite_blobs",
    "agentos.artifacts.sqlite_filesystem",
    "agentos.capabilities.skill_activation",
    "agentos.durable",
    "agentos.memory.sqlite",
    "agentos.planning.sqlite",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == adapter or name.startswith(adapter + ".") for adapter in adapters)
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
