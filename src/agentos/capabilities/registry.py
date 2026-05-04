from agentos.capabilities.tools import RegisteredTool, ToolKind
from agentos.context.projection import ToolDeclaration, ToolGroup
from agentos.providers import ProviderToolSpec


class ToolRegistry:
    """保存可由 provider tool calls 调用的工具。"""

    def __init__(self) -> None:
        """创建空工具 registry。"""

        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        """注册一个工具，工具名必须唯一。"""

        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> RegisteredTool:
        """按名称获取工具。"""

        try:
            return self._tools[tool_name]
        except KeyError as error:
            raise KeyError(tool_name) from error

    def provider_tool_specs(
        self,
        kinds: set[ToolKind] | None = None,
    ) -> list[ProviderToolSpec]:
        """返回 provider request 可使用的工具 schema。"""

        visible_kinds = {"external"} if kinds is None else kinds
        return [
            tool.provider_spec()
            for tool in self._tools.values()
            if tool.kind in visible_kinds
        ]

    def capability_tool_group(self, name: str = "Tools") -> ToolGroup:
        """返回 LLM 可见 Capability Plane 使用的工具摘要。"""

        return ToolGroup(
            name=name,
            tools=[
                ToolDeclaration(name=tool.name, description=tool.description)
                for tool in self._tools.values()
                if tool.kind == "external"
            ],
        )
