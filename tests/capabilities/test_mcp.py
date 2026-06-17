import pytest

from agentos.capabilities import (
    ToolCallRouter,
    ToolRegistry,
    ToolSandboxError,
    WorkspaceToolSandboxPolicy,
)
from agentos.capabilities.executor import ToolExecutionError
from agentos.capabilities.mcp import (
    MCPRegistry,
    MCPServerRegistration,
    MCPToolAdapter,
    MCPToolInfo,
)
from agentos.providers import ProviderToolCall
from agentos.workspace import WorkspaceHandle


class FakeMCPClient:
    def __init__(self, tools: list[MCPToolInfo] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._tools = tools

    def list_tools(self) -> list[MCPToolInfo]:
        if self._tools is not None:
            return list(self._tools)
        return [
            MCPToolInfo(
                name="create_issue",
                description="Create an issue.",
                input_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
        self.calls.append((tool_name, dict(arguments)))
        if "title" not in arguments:
            return "ok"
        return f"{tool_name}:{arguments['title']}"


def test_mcp_registry_exports_provider_specs_and_server_summaries() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            endpoint="stdio:npx github",
            client=client,
        ),
    )

    specs = registry.provider_tool_specs()
    declarations = registry.capability_declarations()

    assert specs[0]["function"]["name"] == "mcp__github__create_issue"
    assert specs[0]["function"]["parameters"]["required"] == ["title"]
    assert declarations[0].name == "github"
    assert declarations[0].tool_prefix == "mcp__github__<tool>"


def test_mcp_tool_adapter_executes_prefixed_provider_call() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    adapter = MCPToolAdapter(registry)

    result = adapter.execute(
        ProviderToolCall(
            id="call_1",
            name="mcp__github__create_issue",
            arguments={"title": "Bug"},
        ),
        prevalidated=True,
    )

    assert result.tool_call_id == "call_1"
    assert result.content == "create_issue:Bug"
    assert client.calls == [("create_issue", {"title": "Bug"})]


def test_mcp_tool_adapter_direct_execute_requires_prevalidation_marker() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    adapter = MCPToolAdapter(registry)

    with pytest.raises(ToolExecutionError, match="ToolCallRouter"):
        adapter.execute(
            ProviderToolCall(
                id="call_1",
                name="mcp__github__create_issue",
                arguments={"title": "Bug"},
            ),
        )

    assert client.calls == []


def test_mcp_registry_rejects_invalid_server_names() -> None:
    registry = MCPRegistry()

    with pytest.raises(ValueError, match="invalid MCP server name"):
        registry.register(
            MCPServerRegistration(
                name="../github",
                description="Bad name.",
                client=FakeMCPClient(),
            ),
        )


def test_mcp_registry_rejects_duplicate_provider_names() -> None:
    class DuplicateClient(FakeMCPClient):
        def list_tools(self) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(
                    name="same",
                    description="First.",
                    input_schema={"type": "object"},
                ),
                MCPToolInfo(
                    name="same",
                    description="Second.",
                    input_schema={"type": "object"},
                ),
            ]

    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=DuplicateClient(),
        ),
    )

    with pytest.raises(ValueError, match="duplicate MCP tool"):
        registry.provider_tool_specs()


def test_mcp_registry_refreshes_automatically_after_register() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    adapter = MCPToolAdapter(registry)

    result = adapter.execute(
        ProviderToolCall(
            id="call_1",
            name="mcp__github__create_issue",
            arguments={"title": "Bug"},
        ),
        prevalidated=True,
    )

    assert result.content == "create_issue:Bug"


def test_tool_router_applies_sandbox_policy_before_mcp_adapter(tmp_path) -> None:
    class FileMCPClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def list_tools(self) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(
                    name="read",
                    description="Read a file.",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                ),
            ]

        def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
            self.calls.append((tool_name, dict(arguments)))
            return "read"

    client = FileMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="docs",
            description="Docs tools.",
            client=client,
        ),
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        mcp_adapter=MCPToolAdapter(registry),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=WorkspaceHandle(
                workspace_id="session:one",
                scope="session",
                root=str(tmp_path / "workspace"),
            ),
            path_rules={"mcp__docs__read": ("path",)},
        ),
    )

    with pytest.raises(ToolSandboxError, match="escapes workspace root"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="mcp__docs__read",
                arguments={"path": "../secret.txt"},
            ),
        )

    assert client.calls == []


def test_tool_router_passes_sandbox_normalized_mcp_paths_to_client(tmp_path) -> None:
    class FileMCPClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def list_tools(self) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(
                    name="read",
                    description="Read a file.",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                ),
            ]

        def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
            self.calls.append((tool_name, dict(arguments)))
            return f"read:{arguments['path']}"

    client = FileMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="docs",
            description="Docs tools.",
            client=client,
        ),
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        mcp_adapter=MCPToolAdapter(registry),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=WorkspaceHandle(
                workspace_id="session:one",
                scope="session",
                root=str(tmp_path),
            ),
            path_rules={"mcp__docs__read": ("path",)},
        ),
    )

    result = router.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="mcp__docs__read",
            arguments={"path": "notes/todo.txt"},
        ),
    )

    expected_path = str((tmp_path / "notes" / "todo.txt").resolve())
    assert result.content == f"read:{expected_path}"
    assert client.calls == [("read", {"path": expected_path})]


def test_tool_router_validates_mcp_required_arguments_before_client_call() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        mcp_adapter=MCPToolAdapter(registry),
    )

    with pytest.raises(ToolExecutionError, match="missing required tool argument"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="mcp__github__create_issue",
                arguments={},
            ),
        )

    assert client.calls == []


def test_tool_router_validates_mcp_argument_types_before_client_call() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        mcp_adapter=MCPToolAdapter(registry),
    )

    with pytest.raises(ToolExecutionError, match="invalid tool argument title"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="mcp__github__create_issue",
                arguments={"title": 42},
            ),
        )

    assert client.calls == []


def test_tool_router_applies_capability_allowlist_to_mcp_tools(tmp_path) -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        mcp_adapter=MCPToolAdapter(registry),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=WorkspaceHandle(
                workspace_id="session:one",
                scope="session",
                root=str(tmp_path),
            ),
            allowed_capabilities=frozenset({"filesystem.read"}),
            tool_capabilities={"mcp__github__create_issue": "remote.github.write"},
        ),
    )

    with pytest.raises(ToolSandboxError, match="capability not allowed"):
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="mcp__github__create_issue",
                arguments={"title": "Bug"},
            ),
        )

    assert client.calls == []


def test_tool_router_uses_mcp_tool_declared_capability_for_allowlist(tmp_path) -> None:
    client = FakeMCPClient(
        tools=[
            MCPToolInfo(
                name="read_file",
                description="Read a workspace file.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                capability="filesystem.read",
            ),
        ],
    )
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="files",
            description="Read workspace files.",
            client=client,
        ),
    )
    router = ToolCallRouter(
        tool_registry=ToolRegistry(),
        mcp_adapter=MCPToolAdapter(registry),
        sandbox_policy=WorkspaceToolSandboxPolicy(
            workspace=WorkspaceHandle(
                workspace_id="session:one",
                scope="session",
                root=str(tmp_path),
            ),
            allowed_capabilities=frozenset({"filesystem.read"}),
        ),
    )

    result = router.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="mcp__files__read_file",
            arguments={"path": "notes/todo.txt"},
        ),
    )

    assert result.content == "ok"
    assert client.calls == [("read_file", {"path": "notes/todo.txt"})]
