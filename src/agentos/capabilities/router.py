import asyncio
from dataclasses import dataclass, field

from agentos.attachments.types import AttachmentError
from agentos.capabilities.executor import ToolExecutionResult, ToolExecutor
from agentos.capabilities.mcp import MCPToolAdapter
from agentos.capabilities.registry import ToolRegistry
from agentos.context import ContextRuntime, WorkingStateField
from agentos.context_protocol import (
    CONTEXT_PROTOCOL_TOOL_NAMES,
    context_protocol_tool_specs,
)
from agentos.messages import Message
from agentos.policies import SecurityPolicy
from agentos.providers import ProviderToolCall, ProviderToolSpec
from agentos.recall import RecallRuntime


@dataclass(slots=True)
class ToolCallRouter:
    """Route context tools, external tools, MCP tools, and skill tools."""

    tool_registry: ToolRegistry
    context_runtime: ContextRuntime | None = None
    recall_runtime: RecallRuntime | None = None
    attachment_runtime: object | None = None
    mcp_adapter: MCPToolAdapter | None = None
    security_policy: SecurityPolicy = field(default_factory=SecurityPolicy)
    _executor: ToolExecutor | None = None

    def tool_specs(self) -> list[ProviderToolSpec]:
        """Return provider schemas for context protocol tools and registered tools."""

        return [
            *context_protocol_tool_specs(),
            *self.tool_registry.provider_tool_specs(kinds={"external", "skill"}),
            *(
                self.mcp_adapter.provider_tool_specs()
                if self.mcp_adapter is not None
                else []
            ),
        ]

    def execute_tool_call(self, tool_call: ProviderToolCall) -> ToolExecutionResult:
        """Execute a provider tool call and route it by tool namespace."""

        self.security_policy.ensure_tool_allowed(tool_call.name)
        if tool_call.name in CONTEXT_PROTOCOL_TOOL_NAMES:
            return self._execute_context_tool(tool_call)
        if tool_call.name.startswith("mcp__"):
            if self.mcp_adapter is None:
                raise RuntimeError("mcp adapter is required for MCP tool calls")
            return self.mcp_adapter.execute(tool_call)
        return self._tool_executor().execute(tool_call)

    async def async_execute_tool_call(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolExecutionResult:
        """Execute provider tool calls asynchronously where supported."""

        if tool_call.name in CONTEXT_PROTOCOL_TOOL_NAMES:
            return self.execute_tool_call(tool_call)
        return await asyncio.to_thread(self.execute_tool_call, tool_call)

    def _tool_executor(self) -> ToolExecutor:
        """Create the external tool executor lazily."""

        if self._executor is None:
            self._executor = ToolExecutor(
                registry=self.tool_registry,
                security_policy=self.security_policy,
            )
        return self._executor

    def _execute_context_tool(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolExecutionResult:
        """Apply a context protocol tool call."""

        if tool_call.name == "recall_context":
            return self._execute_recall_context(tool_call)
        if tool_call.name == "load_image":
            return self._execute_load_image(tool_call)

        if self.context_runtime is None:
            raise RuntimeError("context runtime is required for context tools")

        arguments = tool_call.arguments
        if tool_call.name == "declare_schema":
            self.context_runtime.declare_schema(self._working_state_fields(arguments))
        elif tool_call.name == "update_state":
            self.context_runtime.update_state(
                field_name=str(arguments["field_name"]),
                value=arguments["value"],  # type: ignore[arg-type]
            )
        elif tool_call.name == "extend_schema":
            self.context_runtime.extend_schema(self._working_state_fields(arguments))
        elif tool_call.name == "start_chapter":
            fields = arguments.get("fields")
            self.context_runtime.start_chapter(
                None if fields is None else self._working_state_fields(arguments),
            )
        else:
            raise RuntimeError(f"unknown context tool: {tool_call.name}")

        return ToolExecutionResult(
            tool_call_id=tool_call.id,
            content=f"context tool {tool_call.name} applied",
        )

    def _execute_recall_context(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolExecutionResult:
        """Return recalled text/history as this tool call's result."""

        if self.recall_runtime is None:
            raise RuntimeError("recall runtime is required for recall_context")
        arguments = tool_call.arguments
        handle = arguments.get("handle")
        query = arguments.get("query")
        limit = int(arguments.get("limit", 1))
        recalled_messages = self.recall_runtime.recall_context(
            None if handle is None else str(handle),
            query=None if query is None else str(query),
            limit=limit,
        )
        return ToolExecutionResult(
            tool_call_id=tool_call.id,
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
        messages: list[Message],
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

    def _execute_load_image(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolExecutionResult:
        """Schedule an uploaded image for the next provider request."""

        if self.attachment_runtime is None:
            raise RuntimeError("attachment runtime is required for load_image")
        load_image_handle = getattr(
            self.attachment_runtime,
            "load_image_handle",
            None,
        )
        if not callable(load_image_handle):
            raise RuntimeError(
                "attachment_runtime must define load_image_handle()",
            )
        handle = tool_call.arguments.get("handle")
        if not isinstance(handle, str):
            return ToolExecutionResult(
                tool_call_id=tool_call.id,
                content="load_image failed: handle is required",
            )
        try:
            attachment = load_image_handle(handle)
        except AttachmentError as error:
            return ToolExecutionResult(
                tool_call_id=tool_call.id,
                content=f"load_image failed: {error}",
            )
        attachment_handle = str(getattr(attachment, "handle", handle))
        return ToolExecutionResult(
            tool_call_id=tool_call.id,
            content=(
                "load_image applied; scheduled "
                f"{attachment_handle} for next provider request"
            ),
        )

    def _working_state_fields(
        self,
        arguments: dict[str, object],
    ) -> list[WorkingStateField]:
        """Parse working state field declarations from provider arguments."""

        raw_fields = arguments.get("fields")
        if not isinstance(raw_fields, list):
            raise ValueError("context schema tools require a fields list")
        fields: list[WorkingStateField] = []
        for raw_field in raw_fields:
            if not isinstance(raw_field, dict):
                raise ValueError("working state field must be an object")
            fields.append(
                WorkingStateField(
                    name=str(raw_field["name"]),
                    type=str(raw_field["type"]),
                    purpose=str(raw_field["purpose"]),
                ),
            )
        return fields

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
