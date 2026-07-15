from __future__ import annotations

import ast
import inspect
from pathlib import Path

from agentos.context.registry import TrustedSkillInstructionProvider
from agentos.runtime.provider_request_builder import ContextProjectionProvider


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_kernel_does_not_import_extension_implementations() -> None:
    forbidden = (
        "agentos.capabilities.skill_runtime",
        "agentos.memory",
        "agentos.planning",
    )
    checked_roots = (
        PROJECT_ROOT / "src" / "agentos" / "runtime",
        PROJECT_ROOT / "src" / "agentos" / "context",
    )

    matches: list[str] = []
    for root in checked_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module is not None:
                    imports.append(node.module)
                elif isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
            for imported in imports:
                if imported.startswith(forbidden):
                    matches.append(
                        f"{path.relative_to(PROJECT_ROOT)} imports {imported}",
                    )

    assert matches == []


def test_extension_provider_ports_keep_request_bound_no_arg_methods() -> None:
    assert tuple(
        inspect.signature(TrustedSkillInstructionProvider.items).parameters,
    ) == ("self",)
    assert tuple(
        inspect.signature(ContextProjectionProvider.projections).parameters,
    ) == ("self",)


def test_recall_domain_does_not_import_memory() -> None:
    recall_root = PROJECT_ROOT / "src" / "agentos" / "recall"
    matches: list[str] = []

    for path in recall_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = node.module
                if imported.startswith("agentos.memory"):
                    matches.append(
                        f"{path.relative_to(PROJECT_ROOT)} imports {imported}",
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("agentos.memory"):
                        matches.append(
                            f"{path.relative_to(PROJECT_ROOT)} imports {alias.name}",
                        )

    assert matches == []


def test_memory_domain_does_not_import_recall_or_session_storage() -> None:
    forbidden = (
        "agentos.compression",
        "agentos.messages",
        "agentos.persistence",
        "agentos.providers",
        "agentos.recall",
        "agentos.runtime",
    )
    memory_root = PROJECT_ROOT / "src" / "agentos" / "memory"
    matches: list[str] = []

    for path in memory_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imports: tuple[str, ...] = ()
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imports = (node.module,)
            elif isinstance(node, ast.Import):
                imports = tuple(alias.name for alias in node.names)
            for imported in imports:
                if imported.startswith(forbidden):
                    matches.append(
                        f"{path.relative_to(PROJECT_ROOT)} imports {imported}",
                    )

    assert matches == []


def test_recall_and_session_storage_have_single_domain_owners() -> None:
    removed_memory_modules = (
        "embeddings.py",
        "qdrant_index.py",
        "recall_index.py",
        "redis_store.py",
        "serializers.py",
        "store.py",
        "types.py",
    )
    memory_root = PROJECT_ROOT / "src" / "agentos" / "memory"
    assert all(
        not (memory_root / module_name).exists()
        for module_name in removed_memory_modules
    )

    required_modules = (
        "src/agentos/recall/types.py",
        "src/agentos/recall/index.py",
        "src/agentos/recall/store.py",
        "src/agentos/recall/segment_repository.py",
        "src/agentos/recall/in_memory_index.py",
        "src/agentos/recall/embeddings.py",
        "src/agentos/recall/qdrant_index.py",
        "src/agentos/persistence/session_store.py",
        "src/agentos/persistence/in_memory_session.py",
        "src/agentos/persistence/redis_session.py",
        "src/agentos/persistence/session_serializers.py",
    )
    assert all((PROJECT_ROOT / module).is_file() for module in required_modules)


def test_recall_runtime_names_segment_repository_dependency() -> None:
    from agentos.recall import RecallRuntime

    parameters = inspect.signature(RecallRuntime).parameters

    assert "segment_repository" in parameters
    assert "compression_index" not in parameters
    assert "memory_runtime" not in parameters
