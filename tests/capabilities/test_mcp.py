import pytest

from agentos.capabilities.mcp import (
    MCPRegistry,
    MCPServerRegistration,
    MCPToolAdapter,
    MCPToolInfo,
)
from agentos.providers import ProviderToolCall


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_tools(self) -> list[MCPToolInfo]:
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
    )

    assert result.tool_call_id == "call_1"
    assert result.content == "create_issue:Bug"
    assert client.calls == [("create_issue", {"title": "Bug"})]


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
    )

    assert result.content == "create_issue:Bug"
