from dataclasses import dataclass, field

from agentos._json_values import thaw_json
from agentos.capabilities.backend import ExecutionBackend, InProcessExecutionBackend
from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.executor import (
    ToolExecutionOutcome,
    ToolExecutionResult,
    ToolExecutor,
    ToolExecutionError,
    validate_tool_arguments,
)
from agentos.capabilities.mcp import MCPToolAdapter
from agentos.capabilities.registry import ToolRegistry
from agentos.capabilities.sandbox import ToolSandboxPolicy
from agentos.capabilities.tools import (
    SideEffectPolicy,
    ToolConcurrencyPolicy,
    ToolExecutionContract,
)
from agentos.context import ContextRuntime
from agentos.context.tool_mutations import apply_context_mutation
from agentos.context_protocol import (
    CONTEXT_PROTOCOL_TOOL_NAMES,
    context_protocol_tool_specs,
)
from agentos.messages import StoredMessage
from agentos.policies import ResourcePolicy, SecurityPolicy
from agentos.providers import ProviderToolCall, ProviderToolSpec
from agentos.recall import RecallRuntime


@dataclass(slots=True)
class ToolCallRouter:
    """统一路由 context tools 和外部工具。"""

    tool_registry: ToolRegistry
    context_runtime: ContextRuntime | None = None
    recall_runtime: RecallRuntime | None = None
    mcp_adapter: MCPToolAdapter | None = None
    security_policy: SecurityPolicy = field(default_factory=SecurityPolicy)
    backend: ExecutionBackend = field(default_factory=InProcessExecutionBackend)
    resource_policy: ResourcePolicy = field(default_factory=ResourcePolicy)
    sandbox_policy: ToolSandboxPolicy | None = None
    _executor: ToolExecutor | None = None

    def tool_specs(self) -> list[ProviderToolSpec]:
        """返回 context protocol tools 和外部工具的 provider schema。"""

        return [
            *context_protocol_tool_specs(),
            *self.tool_registry.provider_tool_specs(kinds={"external", "skill"}),
            *(
                self.mcp_adapter.provider_tool_specs()
                if self.mcp_adapter is not None
                else []
            ),
        ]

    def prepare_call(self, call: ProviderToolCall) -> ProviderToolCall:
        """Validate and canonicalize a Provider call before checkpointing."""

        if call.name in CONTEXT_PROTOCOL_TOOL_NAMES:
            self.security_policy.ensure_tool_allowed(call.name)
            spec = next(
                item
                for item in context_protocol_tool_specs()
                if item.function.name == call.name
            )
            arguments = thaw_json(call.arguments)
            validate_tool_arguments(
                call.name,
                arguments,
                spec.function.parameters,
            )
            return ProviderToolCall(call.id, call.name, arguments)
        if call.name.startswith("mcp__"):
            if self.mcp_adapter is None:
                raise ToolExecutionError("mcp adapter is required for MCP tool calls")
            tool = self.mcp_adapter.registered_tool_for(call.name)
        else:
            try:
                tool = self.tool_registry.get(call.name)
            except KeyError as error:
                raise ToolExecutionError(f"unknown tool: {call.name}") from error
        return self._tool_executor().prepare_registered_call(tool, call)

    def concurrency_policy_for(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolConcurrencyPolicy:
        """保守返回单个工具调用的正式并发策略。"""

        if tool_call.name in CONTEXT_PROTOCOL_TOOL_NAMES:
            return ToolConcurrencyPolicy.EXCLUSIVE
        if tool_call.name.startswith("mcp__"):
            if self.mcp_adapter is None:
                return ToolConcurrencyPolicy.EXCLUSIVE
            return self.mcp_adapter.concurrency_policy_for(tool_call.name)
        try:
            return self.tool_registry.get(tool_call.name).concurrency_policy
        except KeyError:
            return ToolConcurrencyPolicy.EXCLUSIVE

    def tool_contract_for(
        self,
        invocation: ToolInvocation,
    ) -> ToolExecutionContract:
        """在执行批次前解析本地可信 Tool contract。"""

        name = invocation.tool_name
        if name in CONTEXT_PROTOCOL_TOOL_NAMES:
            policy = (
                SideEffectPolicy.PURE
                if name == "recall_context"
                else SideEffectPolicy.IDEMPOTENT
            )
            return ToolExecutionContract(
                policy,
                ToolConcurrencyPolicy.EXCLUSIVE,
            )
        if name.startswith("mcp__"):
            if self.mcp_adapter is None:
                raise ToolExecutionError("mcp adapter is required for MCP tool calls")
            return self.mcp_adapter.registered_tool_for(name).execution_contract()
        try:
            return self.tool_registry.get(name).execution_contract()
        except KeyError as error:
            raise ToolExecutionError(f"unknown tool: {name}") from error

    async def execute(
        self,
        invocation: ToolInvocation,
    ) -> ToolExecutionOutcome:
        """按能力类型异步执行 canonical ToolInvocation。"""

        if invocation.tool_name in CONTEXT_PROTOCOL_TOOL_NAMES:
            self.security_policy.ensure_tool_allowed(invocation.tool_name)
            return self._execute_context_tool(invocation)
        if invocation.tool_name.startswith("mcp__"):
            if self.mcp_adapter is None:
                raise RuntimeError("mcp adapter is required for MCP tool calls")
            return await self._tool_executor().execute_registered(
                self.mcp_adapter.registered_tool_for(invocation.tool_name),
                invocation,
            )
        return await self._tool_executor().execute(invocation)

    def _tool_executor(self) -> ToolExecutor:
        """延迟创建外部工具 executor。"""

        if self._executor is None:
            self._executor = ToolExecutor(
                registry=self.tool_registry,
                security_policy=self.security_policy,
                backend=self.backend,
                resource_policy=self.resource_policy,
                sandbox_policy=self.sandbox_policy,
            )
        return self._executor

    def _execute_context_tool(
        self,
        invocation: ToolInvocation,
    ) -> ToolExecutionResult:
        """把 context tool call 应用到 ContextRuntime。"""

        if invocation.tool_name == "recall_context":
            return self._execute_recall_context(invocation)
        if self.context_runtime is None:
            raise RuntimeError("context runtime is required for context tools")

        apply_context_mutation(self.context_runtime, invocation)

        return ToolExecutionResult(
            tool_call_id=invocation.context.tool_call_id,
            content=f"context tool {invocation.tool_name} applied",
        )

    def _execute_recall_context(
        self,
        invocation: ToolInvocation,
    ) -> ToolExecutionResult:
        """把 recall_context 工具调用交给 RecallRuntime。"""

        if self.recall_runtime is None:
            raise RuntimeError("recall runtime is required for recall_context")
        arguments = invocation.arguments
        handle = arguments.get("handle")
        query = arguments.get("query")
        limit = int(arguments.get("limit", 1))
        recalled_messages = self.recall_runtime.recall_context(
            None if handle is None else str(handle),
            query=None if query is None else str(query),
            limit=limit,
        )
        return ToolExecutionResult(
            tool_call_id=invocation.context.tool_call_id,
            content=self._format_recalled_context(
                handle=None if handle is None else str(handle),
                query=None if query is None else str(query),
                messages=recalled_messages,
            ),
        )

    def _format_recalled_context(
        self,
        *,
        handle: str | None,
        query: str | None,
        messages: tuple[StoredMessage, ...],
    ) -> str:
        source = "compressed_history" if handle is not None else "semantic_recall"
        identifier = handle if handle is not None else query or ""
        lines = [
            f'<recalled-context source="{source}" handle="{self._escape_attr(identifier)}">',
        ]
        for message in messages:
            lines.extend(
                [
                    f'  <message role="{message.role}" id="{self._escape_attr(message.id)}">',
                    self._indent_text(message.content, "    "),
                    "  </message>",
                ],
            )
        lines.append("</recalled-context>")
        return "\n".join(lines)

    def _escape_attr(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _indent_text(self, value: str, prefix: str) -> str:
        escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return "\n".join(f"{prefix}{line}" for line in escaped.splitlines() or [""])
