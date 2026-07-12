from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentos.capabilities import (
    RegisteredTool,
    ToolCallRouter,
    ToolPathSandboxRule,
    ToolRegistry,
    ToolSandboxError,
    WorkspaceToolSandboxPolicy,
)
from agentos.providers import ProviderToolCall
from agentos.workspace import WorkspaceHandle


def test_workspace_tool_sandbox_allows_path_inside_workspace(
    tmp_path: Path,
) -> None:
    called: list[str] = []
    workspace = WorkspaceHandle(
        workspace_id="session:one",
        scope="session",
        root=str(tmp_path),
    )
    router = ToolCallRouter(
        tool_registry=_file_registry(called),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=workspace,
            path_rules={"read_file": ("path",)},
        ),
    )

    result = router.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "notes/todo.txt"},
        ),
    )

    expected_path = str((tmp_path / "notes" / "todo.txt").resolve())
    assert result.content == f"read:{expected_path}"
    assert called == [expected_path]


def test_workspace_tool_sandbox_rejects_relative_escape_before_handler(
    tmp_path: Path,
) -> None:
    called: list[str] = []
    workspace = WorkspaceHandle(
        workspace_id="session:one",
        scope="session",
        root=str(tmp_path / "workspace"),
    )
    router = ToolCallRouter(
        tool_registry=_file_registry(called),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=workspace,
            path_rules={"read_file": ("path",)},
        ),
    )

    with pytest.raises(ToolSandboxError, match="escapes workspace root"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "../secret.txt"},
            ),
        )

    assert called == []


def test_workspace_tool_sandbox_rejects_absolute_escape_before_handler(
    tmp_path: Path,
) -> None:
    called: list[str] = []
    outside = tmp_path / "outside.txt"
    workspace = WorkspaceHandle(
        workspace_id="session:one",
        scope="session",
        root=str(tmp_path / "workspace"),
    )
    router = ToolCallRouter(
        tool_registry=_file_registry(called),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=workspace,
            path_rules={"read_file": ("path",)},
        ),
    )

    with pytest.raises(ToolSandboxError, match="escapes workspace root"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": str(outside)},
            ),
        )

    assert called == []


def test_workspace_tool_sandbox_rejects_sandbox_required_tool_without_root() -> None:
    called: list[str] = []
    workspace = WorkspaceHandle(
        workspace_id="session:remote",
        scope="session",
        root=None,
    )
    router = ToolCallRouter(
        tool_registry=_file_registry(called),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=workspace,
            path_rules={"read_file": ("path",)},
        ),
    )

    with pytest.raises(ToolSandboxError, match="requires a workspace root"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "notes/todo.txt"},
            ),
        )

    assert called == []


def test_workspace_tool_sandbox_rejects_unauthorized_capability_before_handler(
    tmp_path: Path,
) -> None:
    called: list[str] = []
    router = ToolCallRouter(
        tool_registry=_shell_registry(called),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=WorkspaceHandle(
                workspace_id="session:one",
                scope="session",
                root=str(tmp_path),
            ),
            allowed_capabilities=frozenset({"filesystem.read"}),
        ),
    )

    with pytest.raises(ToolSandboxError, match="capability not allowed"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="run_shell",
                arguments={"command": "echo hello"},
            ),
        )

    assert called == []


def test_workspace_tool_sandbox_rejects_missing_capability_when_allowlist_is_set(
    tmp_path: Path,
) -> None:
    called: list[str] = []
    router = ToolCallRouter(
        tool_registry=_file_registry(called),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=WorkspaceHandle(
                workspace_id="session:one",
                scope="session",
                root=str(tmp_path),
            ),
            allowed_capabilities=frozenset({"filesystem.read"}),
        ),
    )

    with pytest.raises(ToolSandboxError, match="tool capability is required"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "notes/todo.txt"},
            ),
        )

    assert called == []


def test_workspace_tool_sandbox_enforces_async_execution_path(
    tmp_path: Path,
) -> None:
    called: list[str] = []
    router = ToolCallRouter(
        tool_registry=_async_file_registry(called),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=WorkspaceHandle(
                workspace_id="session:one",
                scope="session",
                root=str(tmp_path / "workspace"),
            ),
            path_rules={"read_file": (ToolPathSandboxRule("path"),)},
        ),
    )

    async def run() -> object:
        return await router.async_execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "../secret.txt"},
            ),
        )

    with pytest.raises(ToolSandboxError, match="escapes workspace root"):
        asyncio.run(run())

    assert called == []


def test_workspace_tool_sandbox_ignores_tools_without_sandbox_requirements() -> None:
    called: list[str] = []
    router = ToolCallRouter(
        tool_registry=_file_registry(called, sensitivity="normal"),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=None,
            path_rules={},
        ),
    )

    result = router.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "../legacy.txt"},
        ),
    )

    assert result.content == "read:../legacy.txt"
    assert called == ["../legacy.txt"]


def _file_registry(
    called: list[str],
    *,
    sensitivity: str = "sandbox-required",
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="read_file",
            description="Read a file.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=lambda arguments: called.append(str(arguments["path"]))
            or f"read:{arguments['path']}",
            metadata={"sensitivity": sensitivity},
        ),
    )
    return registry


def _async_file_registry(called: list[str]) -> ToolRegistry:
    async def read_file(arguments: dict[str, object]) -> str:
        called.append(str(arguments["path"]))
        return f"read:{arguments['path']}"

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="read_file",
            description="Read a file.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=read_file,
            metadata={"sensitivity": "sandbox-required"},
        ),
    )
    return registry


def _shell_registry(called: list[str]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="run_shell",
            description="Run a shell command.",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            handler=lambda arguments: called.append(str(arguments["command"])) or "ok",
            metadata={
                "sensitivity": "sandbox-required",
                "capability": "process.exec",
            },
        ),
    )
    return registry
