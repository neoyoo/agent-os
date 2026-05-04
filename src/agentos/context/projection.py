from dataclasses import dataclass, field


DEFAULT_IDENTITY = "\n".join(
    [
        "你是一个在现有代码库中工作的 AI 工程助手。",
        "修改代码前先阅读相关代码。优先做小范围、可检查的改动。",
        "除非技术标识必须使用英文，否则使用用户的语言进行解释。",
    ],
)

DEFAULT_SECURITY_GUARDRAILS = [
    "除非用户明确要求，否则不要覆盖或回滚用户的改动。",
    "未经明确确认，不要运行破坏性 shell 命令。",
    "不要暴露密钥、凭证、私钥或 token。",
    "如果某个操作可能导致用户工作丢失，先询问再行动。",
]


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    """Runtime Contract 的可配置投影输入。"""

    identity: str = DEFAULT_IDENTITY
    security_guardrails: list[str] = field(
        default_factory=lambda: list(DEFAULT_SECURITY_GUARDRAILS),
    )
    extra_guardrails: list[str] = field(default_factory=list)

    def guardrails(self) -> list[str]:
        """返回默认安全规则和项目追加规则的合并结果。"""

        return [*self.security_guardrails, *self.extra_guardrails]


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    """工具注册表暴露给 prompt 的轻量声明。"""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class ToolGroup:
    """按语义分组后的工具声明集合。"""

    name: str
    tools: list[ToolDeclaration] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MCPServerDeclaration:
    """MCP server 暴露给 prompt 的连接摘要。"""

    name: str
    description: str
    endpoint: str | None = None
    tool_prefix: str | None = None

    def rendered_title(self) -> str:
        """返回包含 endpoint 的 server 标题。"""

        if self.endpoint:
            return f"{self.name} ({self.endpoint})"
        return self.name

    def rendered_tool_prefix(self) -> str:
        """返回该 MCP server 的工具命名前缀。"""

        if self.tool_prefix is not None:
            return self.tool_prefix
        return f"mcp__{self.name}__<tool>"


@dataclass(frozen=True, slots=True)
class SkillDeclaration:
    """Skill frontmatter 暴露给 prompt 的摘要。"""

    name: str
    when_to_use: str


@dataclass(frozen=True, slots=True)
class CapabilityPlane:
    """当前 session 注册能力的 LLM 可见投影。"""

    tool_groups: list[ToolGroup] = field(default_factory=list)
    mcp_servers: list[MCPServerDeclaration] = field(default_factory=list)
    skills: list[SkillDeclaration] = field(default_factory=list)
