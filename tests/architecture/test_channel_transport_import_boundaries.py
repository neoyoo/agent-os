from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHANNEL_ROOT = PROJECT_ROOT / "src" / "agentos" / "channels"


def test_channels_do_not_import_private_transport_modules() -> None:
    violations: dict[str, list[str]] = {}
    for path in CHANNEL_ROOT.glob("*.py"):
        private_imports = sorted(
            module
            for module in _imports(path)
            if module.startswith("agentos.transports.")
            and any(part.startswith("_") for part in module.split(".")[2:])
        )
        if private_imports:
            violations[str(path.relative_to(PROJECT_ROOT))] = private_imports

    assert violations == {}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules
