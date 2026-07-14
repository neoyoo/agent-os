from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeAlias, cast

from agentos._waiting import WaitRequest
from agentos.providers.json_values import thaw_json
from agentos._redaction import is_secret_like_key
from agentos.capabilities.backend import ExecutionBackend, InProcessExecutionBackend
from agentos.capabilities.registry import ToolRegistry
from agentos.capabilities.sandbox import ToolSandboxPolicy
from agentos.capabilities.tools import RegisteredTool
from agentos.policies import ResourcePolicy, SecurityPolicy
from agentos.providers import ProviderToolCall


class ToolExecutionError(RuntimeError):
    """工具执行失败。"""


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """标准化工具执行结果。"""

    tool_call_id: str
    content: str


ToolExecutionOutcome: TypeAlias = ToolExecutionResult | WaitRequest


def validate_tool_arguments(
    tool_name: str,
    arguments: dict[str, object],
    schema: Mapping[str, object],
) -> None:
    """按 SDK 支持的最小 JSON schema 子集校验工具参数。"""

    if schema.get("type") not in {None, "object"}:
        raise ToolExecutionError("tool schema root must be an object")
    required = schema.get("required", [])
    if isinstance(required, Sequence) and not isinstance(required, str):
        for name in required:
            if isinstance(name, str) and name not in arguments:
                raise ToolExecutionError(
                    f"missing required tool argument: {name}",
                )
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return
    for name, value in arguments.items():
        spec = properties.get(name)
        if not isinstance(spec, Mapping):
            continue
        expected = spec.get("type")
        if expected is None or _matches_json_type(value, expected):
            continue
        safe_value = _redact_value(name, value)
        raise ToolExecutionError(
            f"invalid tool argument {name}: expected {expected}, got {safe_value!r}",
        )


@dataclass(slots=True)
class ToolExecutor:
    """执行外部工具，并在执行前应用安全策略。"""

    registry: ToolRegistry
    security_policy: SecurityPolicy
    backend: ExecutionBackend = field(default_factory=InProcessExecutionBackend)
    resource_policy: ResourcePolicy = field(default_factory=ResourcePolicy)
    sandbox_policy: ToolSandboxPolicy | None = None

    def execute(self, tool_call: ProviderToolCall) -> ToolExecutionOutcome:
        """执行 provider tool call 对应的外部工具。"""

        self.security_policy.ensure_tool_allowed(tool_call.name)
        tool = self._tool_for_call(tool_call)
        arguments = cast(dict[str, object], thaw_json(tool_call.arguments))
        self._validate_arguments(tool_call.name, arguments, tool.parameters)
        self._ensure_sandbox_allowed(tool, arguments)
        outcome = self.backend.run(
            tool,
            arguments,
            resource_policy=self.resource_policy,
        )
        if isinstance(outcome, WaitRequest):
            return outcome
        return ToolExecutionResult(
            tool_call_id=tool_call.id,
            content=outcome,
        )

    async def async_execute(self, tool_call: ProviderToolCall) -> ToolExecutionOutcome:
        """异步执行 provider tool call。"""

        self.security_policy.ensure_tool_allowed(tool_call.name)
        tool = self._tool_for_call(tool_call)
        arguments = cast(dict[str, object], thaw_json(tool_call.arguments))
        self._validate_arguments(tool_call.name, arguments, tool.parameters)
        self._ensure_sandbox_allowed(tool, arguments)
        outcome = await self.backend.async_run(
            tool,
            arguments,
            resource_policy=self.resource_policy,
        )
        if isinstance(outcome, WaitRequest):
            return outcome
        return ToolExecutionResult(tool_call_id=tool_call.id, content=outcome)

    def _tool_for_call(self, tool_call: ProviderToolCall) -> RegisteredTool:
        try:
            return self.registry.get(tool_call.name)
        except KeyError as error:
            raise ToolExecutionError(f"unknown tool: {tool_call.name}") from error

    def _ensure_sandbox_allowed(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
    ) -> None:
        if self.sandbox_policy is None:
            return
        self.sandbox_policy.ensure_tool_call_allowed(tool, arguments)

    def _validate_arguments(
        self,
        tool_name: str,
        arguments: dict[str, object],
        schema: dict[str, object],
    ) -> None:
        """执行最小 JSON schema 校验，避免无效参数进入 handler。"""

        validate_tool_arguments(tool_name, arguments, schema)


def _matches_json_type(value: object, expected: object) -> bool:
    if isinstance(expected, (list, tuple)):
        return any(_matches_json_type(value, item) for item in expected)
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


def _redact_value(name: str, value: object) -> object:
    """避免敏感工具参数出现在校验错误中。"""

    if is_secret_like_key(name):
        return "[REDACTED]"
    if not isinstance(value, str):
        return value
    if value.startswith(("sk-", "sk_", "pk-")):
        return "[REDACTED]"
    return value
