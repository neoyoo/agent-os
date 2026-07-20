from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeAlias, cast

from agentos._waiting import WaitRequest
from agentos._json_values import thaw_json
from agentos.capabilities.backend import ExecutionBackend, InProcessExecutionBackend
from agentos.capabilities.invocation import (
    ToolCompensationInvocation,
    ToolInvocation,
)
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

    def __post_init__(self) -> None:
        if type(self.tool_call_id) is not str or not self.tool_call_id.strip():
            raise ValueError("tool_call_id must not be empty")
        if type(self.content) is not str:
            raise TypeError("tool result content must be str")


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
        raise ToolExecutionError(
            f"invalid tool argument {name}: expected {expected}",
        )


@dataclass(slots=True)
class ToolExecutor:
    """执行外部工具，并在执行前应用安全策略。"""

    registry: ToolRegistry
    security_policy: SecurityPolicy
    backend: ExecutionBackend = field(default_factory=InProcessExecutionBackend)
    resource_policy: ResourcePolicy = field(default_factory=ResourcePolicy)
    sandbox_policy: ToolSandboxPolicy | None = None

    async def execute(self, invocation: ToolInvocation) -> ToolExecutionOutcome:
        """异步执行 canonical ToolInvocation。"""

        tool = self._tool_for_name(invocation.tool_name)
        return await self.execute_registered(tool, invocation)

    async def execute_registered(
        self,
        tool: RegisteredTool,
        invocation: ToolInvocation,
    ) -> ToolExecutionOutcome:
        """执行 Router 已解析的单个 RegisteredTool。"""

        prepared = self.prepare_registered_call(
            tool,
            ProviderToolCall(
                invocation.context.tool_call_id,
                invocation.tool_name,
                invocation.arguments,
            ),
        )
        prepared_invocation = invocation
        if prepared.arguments != invocation.arguments:
            prepared_invocation = ToolInvocation(
                invocation.tool_name,
                prepared.arguments,
                invocation.context,
            )
        outcome = await self.backend.execute(
            tool,
            prepared_invocation,
            resource_policy=self.resource_policy,
        )
        if isinstance(outcome, WaitRequest):
            return outcome
        return ToolExecutionResult(
            tool_call_id=invocation.context.tool_call_id,
            content=outcome,
        )

    def prepare_registered_call(
        self,
        tool: RegisteredTool,
        call: ProviderToolCall,
    ) -> ProviderToolCall:
        """Validate and freeze the arguments that execution will observe."""

        if tool.name != call.name:
            raise ToolExecutionError("tool declaration does not match invocation")
        self.security_policy.ensure_tool_allowed(call.name)
        arguments = cast(dict[str, object], thaw_json(call.arguments))
        self._validate_arguments(call.name, arguments, tool.parameters)
        self._ensure_sandbox_allowed(tool, arguments)
        return ProviderToolCall(call.id, call.name, arguments)

    async def execute_compensation(
        self,
        tool: RegisteredTool,
        invocation: ToolCompensationInvocation,
    ) -> None:
        """执行已注册 compensatable Tool 的 typed 补偿 handler。"""

        try:
            registered = self.registry.get(tool.name)
        except KeyError as error:
            raise ToolExecutionError("compensation tool is not registered") from error
        if registered is not tool or tool.compensation_handler is None:
            raise ToolExecutionError("tool does not declare compensation")
        await self.backend.execute_compensation(
            tool,
            invocation,
            resource_policy=self.resource_policy,
        )

    def _tool_for_name(self, tool_name: str) -> RegisteredTool:
        try:
            return self.registry.get(tool_name)
        except KeyError as error:
            raise ToolExecutionError(f"unknown tool: {tool_name}") from error

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
