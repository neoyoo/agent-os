from dataclasses import dataclass, field


class SecurityPolicyError(PermissionError):
    """工具调用被安全策略拒绝。"""


@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    """最小工具安全策略：deny 优先，allow 可选。"""

    allowed_tools: set[str] | None = None
    denied_tools: set[str] = field(default_factory=set)

    def ensure_tool_allowed(self, tool_name: str) -> None:
        """在工具执行前检查权限。"""

        if tool_name in self.denied_tools:
            raise SecurityPolicyError(f"tool denied by security policy: {tool_name}")
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            raise SecurityPolicyError(
                f"tool not allowed by security policy: {tool_name}",
            )
