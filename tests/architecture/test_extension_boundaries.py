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
