from __future__ import annotations

import ast
from pathlib import Path

import agentos.transports.a2a as a2a


ROOT = Path(__file__).parents[3] / "src" / "agentos" / "transports" / "a2a"
BANNED_MODULES = {
    "agentos.channels",
    "agentos.distributed.postgres",
    "agentos.distributed.redis",
    "agentos.distributed.worker",
    "agentos.runtime.agent",
    "agentos.runtime.query_loop",
    "aiohttp",
    "httpx",
    "psycopg",
    "redis",
    "requests",
    "urllib.request",
}
BANNED_NAMES = {"Agent", "Daemon", "QueryLoop", "RunStore", "Worker"}


def test_public_api_exports_only_current_a2a_v1_types_and_codecs() -> None:
    required = {
        "A2AAgentCard",
        "A2AMessage",
        "A2AOperationRequest",
        "A2APart",
        "A2AStreamResponse",
        "A2ATask",
        "decode_operation_request",
        "decode_stream_response",
        "decode_stream_operation_response",
        "encode_operation_response",
        "encode_stream_response",
    }

    assert required <= set(a2a.__all__)
    assert not hasattr(a2a, "A2AMessagePart")
    assert not hasattr(a2a, "A2ASetTaskPushNotificationConfigParams")


def test_a2a_transport_import_graph_has_no_runtime_or_backend_owners() -> None:
    for path in sorted(ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(
                    module == banned or module.startswith(f"{banned}.")
                    for banned in BANNED_MODULES
                ), path
                assert not ({alias.name for alias in node.names} & BANNED_NAMES), path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(
                        alias.name == banned or alias.name.startswith(f"{banned}.")
                        for banned in BANNED_MODULES
                    ), path
