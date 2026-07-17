from __future__ import annotations

from collections.abc import Awaitable, Callable

from agentos.capabilities import ToolCallRouter, ToolConcurrencyPolicy
from agentos.context_protocol import CONTEXT_PROTOCOL_TOOL_NAMES
from agentos.observability.attributes import (
    apply_common_observability_attributes,
    metadata_identity_payload,
)
from agentos.observability.config import CapturePolicy, json_attribute
from agentos.observability.conventions import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
)
from agentos.observability.snapshots import (
    ToolCallSnapshot,
    ToolResultSnapshot,
    build_tool_call_snapshot,
    build_tool_result_snapshot,
)
from agentos.observability.tracer import Tracer
from agentos.providers import ProviderToolCall


class InstrumentedToolCallRouter:
    """在 Tool Routing Boundary 上创建 Tool Span。"""

    def __init__(
        self,
        inner: ToolCallRouter,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 Router 和观测配置。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    def execute_tool_call(self, tool_call: ProviderToolCall) -> object:
        """执行 Tool Call，并记录 Tool Span。"""

        return self._record_tool_call(
            tool_call,
            lambda: self._inner.execute_tool_call(tool_call),
        )

    async def async_execute_tool_call(self, tool_call: ProviderToolCall) -> object:
        """异步执行 Tool Call，并记录 Tool Span。"""

        return await self._record_async_tool_call(
            tool_call,
            lambda: self._inner.async_execute_tool_call(tool_call),
        )

    def concurrency_policy_for(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolConcurrencyPolicy:
        """返回底层 Router 的显式并发策略。"""

        return self._inner.concurrency_policy_for(tool_call)

    def _record_tool_call(
        self,
        tool_call: ProviderToolCall,
        execute: Callable[[], object],
    ) -> object:
        call_snapshot = build_tool_call_snapshot(tool_call, self._capture_policy)
        with self._tracer.start_span(
            f"tool.{tool_call.name}",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "tool",
                GEN_AI_OPERATION_NAME: "execute_tool",
                GEN_AI_TOOL_NAME: tool_call.name,
                GEN_AI_TOOL_CALL_ID: tool_call.id,
                "tool.name": tool_call.name,
                "tool.call_id": tool_call.id,
                "agentos.tool.kind": self._tool_kind(tool_call.name),
                "agentos.tool.arguments.sha256": call_snapshot.arguments_sha256,
            },
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._tool_input_payload(call_snapshot),
                    policy=self._capture_policy,
                ),
            )
            result = execute()
            result_snapshot = build_tool_result_snapshot(
                result,
                self._capture_policy,
            )
            span.set_attribute(
                "agentos.tool.result.sha256",
                result_snapshot.content_sha256,
            )
            span.set_attribute(
                "agentos.tool.result.length",
                result_snapshot.content_length,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_OUTPUT,
                json_attribute(
                    self._tool_output_payload(result_snapshot),
                    policy=self._capture_policy,
                ),
            )
            return result

    async def _record_async_tool_call(
        self,
        tool_call: ProviderToolCall,
        execute: Callable[[], Awaitable[object]],
    ) -> object:
        call_snapshot = build_tool_call_snapshot(tool_call, self._capture_policy)
        with self._tracer.start_span(
            f"tool.{tool_call.name}",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "tool",
                GEN_AI_OPERATION_NAME: "execute_tool",
                GEN_AI_TOOL_NAME: tool_call.name,
                GEN_AI_TOOL_CALL_ID: tool_call.id,
                "tool.name": tool_call.name,
                "tool.call_id": tool_call.id,
                "agentos.tool.kind": self._tool_kind(tool_call.name),
                "agentos.tool.arguments.sha256": call_snapshot.arguments_sha256,
            },
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._tool_input_payload(call_snapshot),
                    policy=self._capture_policy,
                ),
            )
            result = await execute()
            result_snapshot = build_tool_result_snapshot(
                result,
                self._capture_policy,
            )
            span.set_attribute(
                "agentos.tool.result.sha256",
                result_snapshot.content_sha256,
            )
            span.set_attribute(
                "agentos.tool.result.length",
                result_snapshot.content_length,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_OUTPUT,
                json_attribute(
                    self._tool_output_payload(result_snapshot),
                    policy=self._capture_policy,
                ),
            )
            return result

    def tool_specs(self) -> object:
        """透传 Provider Tool Schemas。"""

        return self._inner.tool_specs()

    def _tool_kind(self, tool_name: str) -> str:
        """识别 Tool Kind。"""

        if tool_name in CONTEXT_PROTOCOL_TOOL_NAMES:
            return "context"
        if tool_name.startswith("mcp__"):
            return "mcp"
        try:
            return self._inner.tool_registry.get(tool_name).kind
        except KeyError:
            return "unknown"

    def _tool_input_payload(self, snapshot: ToolCallSnapshot) -> dict[str, object]:
        """返回 Tool Span Input Payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                **metadata_identity_payload(capture_policy=self._capture_policy),
                "arguments_hidden": True,
            }
        return {"arguments": snapshot.arguments}

    def _tool_output_payload(self, snapshot: ToolResultSnapshot) -> dict[str, object]:
        """返回 Tool Span Output Payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                **metadata_identity_payload(capture_policy=self._capture_policy),
                "content_chars": snapshot.content_length,
            }
        return {"content": snapshot.content}
