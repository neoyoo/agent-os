from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2] / "src" / "agentos" / "transports"
BANNED_MODULES = {
    "agentos.channels",
    "agentos.distributed.postgres",
    "agentos.distributed.redis",
    "agentos.distributed.worker",
    "agentos.runtime.agent",
    "agentos.runtime.query_loop",
}
BANNED_NAMES = {"Agent", "QueryLoop", "RunStore", "Worker", "Daemon"}


def test_http_and_sse_transport_import_graph_is_pure() -> None:
    for path in (*sorted((ROOT / "http").glob("*.py")), *sorted((ROOT / "sse").glob("*.py"))):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(
                    module == banned or module.startswith(f"{banned}.")
                    for banned in BANNED_MODULES
                ), path
                assert not ({alias.name for alias in node.names} & BANNED_NAMES), path
