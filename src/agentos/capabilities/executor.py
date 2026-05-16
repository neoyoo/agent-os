from dataclasses import dataclass

from agentos.capabilities.registry import ToolRegistry
from agentos.policies import SecurityPolicy
from agentos.providers import ProviderToolCall


class ToolExecutionError(RuntimeError):
    """工具执行失败。"""


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """标准化工具执行结果。"""

    tool_call_id: str
    content: str


@dataclass(slots=True)
class ToolExecutor:
    """执行外部工具，并在执行前应用安全策略。"""

    registry: ToolRegistry
    security_policy: SecurityPolicy

    def execute(self, tool_call: ProviderToolCall) -> ToolExecutionResult:
        """执行 provider tool call 对应的外部工具。"""

        self.security_policy.ensure_tool_allowed(tool_call.name)
        try:
            tool = self.registry.get(tool_call.name)
        except KeyError as error:
            raise ToolExecutionError(f"unknown tool: {tool_call.name}") from error
        self._validate_arguments(tool_call, tool.parameters)
        return ToolExecutionResult(
            tool_call_id=tool_call.id,
            content=tool.handler(dict(tool_call.arguments)),
        )

    def _validate_arguments(
        self,
        tool_call: ProviderToolCall,
        schema: dict[str, object],
    ) -> None:
        """执行最小 JSON schema 校验，避免无效参数进入 handler。"""

        if schema.get("type") not in {None, "object"}:
            raise ToolExecutionError("tool schema root must be an object")
        arguments = tool_call.arguments
        required = schema.get("required", [])
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in arguments:
                    raise ToolExecutionError(
                        f"missing required tool argument: {name}",
                    )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return
        for name, value in arguments.items():
            spec = properties.get(name)
            if not isinstance(spec, dict):
                continue
            expected = spec.get("type")
            if expected is None or self._matches_json_type(value, expected):
                continue
            safe_value = self._redact_value(value)
            raise ToolExecutionError(
                f"invalid tool argument {name}: expected {expected}, got {safe_value!r}",
            )

    def _matches_json_type(self, value: object, expected: object) -> bool:
        if isinstance(expected, list):
            return any(self._matches_json_type(value, item) for item in expected)
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "object":
            return isinstance(value, dict)
        if expected == "array":
            return isinstance(value, list)
        if expected == "null":
            return value is None
        return True

    def _redact_value(self, value: object) -> object:
        """避免敏感 tool 参数出现在错误消息。"""

        if not isinstance(value, str):
            return value
        if value.startswith(("sk-", "sk_", "pk-")):
            return "[REDACTED]"
        return value
