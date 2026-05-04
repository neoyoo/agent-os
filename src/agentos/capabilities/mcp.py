from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from agentos.capabilities.executor import ToolExecutionResult
from agentos.context.projection import MCPServerDeclaration
from agentos.providers import ProviderToolCall, ProviderToolSpec


_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class MCPToolInfo:
    """MCP server 暴露的单个工具元数据。"""

    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)


class MCPClient(Protocol):
    """ToolCallRouter 视角下的 MCP client 边界。"""

    def list_tools(self) -> list[MCPToolInfo]:
        """返回当前 server 的工具列表。"""

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
        """调用 server-local MCP tool 并返回文本结果。"""


@dataclass(frozen=True, slots=True)
class MCPServerRegistration:
    """一个 MCP server 的注册信息。"""

    name: str
    description: str
    client: MCPClient
    endpoint: str | None = None
    allowed_tools: set[str] | None = None


class MCPRegistry:
    """保存 MCP server 注册信息和 provider-facing MCP tool schemas。"""

    def __init__(self) -> None:
        """创建空 MCP registry。"""

        self._servers: dict[str, MCPServerRegistration] = {}
        self._tools: dict[str, tuple[MCPServerRegistration, MCPToolInfo]] = {}
        self._stale = False

    def register(self, server: MCPServerRegistration) -> None:
        """注册一个 MCP server，server 名称必须唯一且可用于 provider tool name。"""

        _validate_mcp_name(server.name, "server")
        if server.name in self._servers:
            raise ValueError(f"duplicate MCP server: {server.name}")
        self._servers[server.name] = server
        self._stale = True

    def refresh(self) -> None:
        """从已注册 server 刷新 MCP tool schemas。"""

        refreshed: dict[str, tuple[MCPServerRegistration, MCPToolInfo]] = {}
        for server in self._servers.values():
            for tool in server.client.list_tools():
                _validate_mcp_name(tool.name, "tool")
                if server.allowed_tools is not None and tool.name not in server.allowed_tools:
                    continue
                provider_name = self.provider_tool_name(server.name, tool.name)
                if provider_name in refreshed:
                    raise ValueError(f"duplicate MCP tool: {provider_name}")
                refreshed[provider_name] = (server, tool)
        self._tools = refreshed
        self._stale = False

    def provider_tool_specs(self) -> list[ProviderToolSpec]:
        """返回 provider request 可使用的 MCP tool schemas。"""

        self._ensure_fresh()
        return [
            {
                "type": "function",
                "function": {
                    "name": provider_name,
                    "description": (
                        f"MCP server `{server.name}` tool `{tool.name}`. "
                        f"{tool.description}"
                    ).strip(),
                    "parameters": self._normalized_schema(tool.input_schema),
                },
            }
            for provider_name, (server, tool) in self._tools.items()
        ]

    def capability_declarations(self) -> list[MCPServerDeclaration]:
        """返回 LLM 可见 Capability Plane 使用的 MCP server 摘要。"""

        return [
            MCPServerDeclaration(
                name=server.name,
                description=server.description,
                endpoint=server.endpoint,
                tool_prefix=f"mcp__{server.name}__<tool>",
            )
            for server in self._servers.values()
        ]

    def resolve_provider_tool(
        self,
        provider_name: str,
    ) -> tuple[MCPServerRegistration, str]:
        """把 provider tool name 解析为 MCP server 和 server-local tool name。"""

        try:
            self._ensure_fresh()
            server, tool = self._tools[provider_name]
        except KeyError as error:
            raise KeyError(provider_name) from error
        return server, tool.name

    @staticmethod
    def provider_tool_name(server_name: str, tool_name: str) -> str:
        """返回 MCP tool 在 provider tools 参数中的名称。"""

        return f"mcp__{server_name}__{tool_name}"

    def _normalized_schema(self, schema: dict[str, object]) -> dict[str, object]:
        """确保 MCP schema 至少是 JSON object schema。"""

        if schema.get("type") == "object":
            return dict(schema)
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }

    def _ensure_fresh(self) -> None:
        """需要时自动刷新工具缓存。"""

        if self._stale:
            self.refresh()


@dataclass(slots=True)
class MCPToolAdapter:
    """执行 provider MCP tool call 的 adapter。"""

    registry: MCPRegistry

    def provider_tool_specs(self) -> list[ProviderToolSpec]:
        """返回 adapter 可执行的 MCP provider tool schemas。"""

        return self.registry.provider_tool_specs()

    def execute(self, tool_call: ProviderToolCall) -> ToolExecutionResult:
        """执行一个 MCP provider tool call。"""

        server, local_tool_name = self.registry.resolve_provider_tool(tool_call.name)
        content = server.client.call_tool(local_tool_name, dict(tool_call.arguments))
        return ToolExecutionResult(
            tool_call_id=tool_call.id,
            content=content,
        )


def _validate_mcp_name(name: str, kind: str) -> None:
    """校验 MCP server/tool 名称可安全组成 provider tool name。"""

    if not _MCP_NAME_RE.match(name):
        raise ValueError(f"invalid MCP {kind} name: {name}")
