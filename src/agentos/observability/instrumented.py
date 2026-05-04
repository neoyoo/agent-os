from __future__ import annotations

from dataclasses import asdict

from agentos.capabilities import ToolCallRouter
from agentos.context_protocol import CONTEXT_PROTOCOL_TOOL_NAMES
from agentos.observability.config import CapturePolicy, json_attribute
from agentos.observability.conventions import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_MODEL_NAME,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_OBSERVATION_USAGE_DETAILS,
    LANGFUSE_SESSION_ID,
    LANGFUSE_TRACE_INPUT,
    LANGFUSE_TRACE_NAME,
    LANGFUSE_TRACE_OUTPUT,
)
from agentos.observability.snapshots import (
    ProviderRequestSnapshot,
    ProviderResponseSnapshot,
    ToolCallSnapshot,
    ToolResultSnapshot,
    build_provider_request_snapshot,
    build_provider_response_snapshot,
    build_tool_call_snapshot,
    build_tool_result_snapshot,
    stable_sha256,
)
from agentos.observability.tracer import Tracer
from agentos.providers import (
    Provider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)
from agentos.runtime import ProviderRequestBuilder, QueryLoop


class InstrumentedProvider:
    """在 provider boundary 上创建 generation span。"""

    def __init__(
        self,
        inner: Provider,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 provider 和观测配置。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用 provider，并记录 provider.complete span。"""

        request_snapshot = build_provider_request_snapshot(
            request,
            self._capture_policy,
        )
        with self._tracer.start_span(
            "provider.complete",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "generation",
                GEN_AI_OPERATION_NAME: "chat",
                "agentos.provider_request.system.length": request_snapshot.system_length,
                "agentos.provider_request.messages.count": request_snapshot.message_count,
                "agentos.provider_request.tools.count": request_snapshot.tool_count,
                "agentos.provider_request.system.sha256": request_snapshot.system_sha256,
                "agentos.provider_request.messages.sha256": request_snapshot.messages_sha256,
                "agentos.provider_request.tools.sha256": request_snapshot.tools_sha256,
            },
        ) as span:
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._provider_input_payload(request_snapshot),
                    policy=self._capture_policy,
                ),
            )
            response = self._inner.complete(request)
            response_snapshot = build_provider_response_snapshot(
                response,
                self._capture_policy,
            )
            provider_name = response_snapshot.provider_name or "unknown"
            model = response_snapshot.model or "unknown"
            span.set_attributes(
                {
                    GEN_AI_PROVIDER_NAME: provider_name,
                    GEN_AI_REQUEST_MODEL: model,
                    GEN_AI_RESPONSE_MODEL: model,
                    LANGFUSE_OBSERVATION_MODEL_NAME: model,
                    GEN_AI_RESPONSE_FINISH_REASONS: (
                        []
                        if response_snapshot.stop_reason is None
                        else [response_snapshot.stop_reason]
                    ),
                    "agentos.provider.tool_call_count": len(
                        response_snapshot.tool_calls,
                    ),
                },
            )
            if response_snapshot.response_id is not None:
                span.set_attribute(GEN_AI_RESPONSE_ID, response_snapshot.response_id)
                span.set_attribute(
                    "agentos.provider.response_id",
                    response_snapshot.response_id,
                )
            if response_snapshot.usage is not None:
                self._set_usage_attributes(span, response_snapshot.usage)
            span.set_attribute(
                LANGFUSE_OBSERVATION_OUTPUT,
                json_attribute(
                    self._provider_output_payload(response_snapshot),
                    policy=self._capture_policy,
                ),
            )
            return response

    def _provider_input_payload(
        self,
        snapshot: ProviderRequestSnapshot,
    ) -> dict[str, object]:
        """返回 provider span input payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                "system_length": snapshot.system_length,
                "system_sha256": snapshot.system_sha256,
                "message_count": snapshot.message_count,
                "messages_sha256": snapshot.messages_sha256,
                "tool_count": snapshot.tool_count,
                "tools_sha256": snapshot.tools_sha256,
            }
        return {
            "system": snapshot.system,
            "messages": snapshot.messages,
            "tools": snapshot.tools,
        }

    def _provider_output_payload(
        self,
        snapshot: ProviderResponseSnapshot,
    ) -> dict[str, object]:
        """返回 provider span output payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                "content_length": snapshot.content_length,
                "content_sha256": snapshot.content_sha256,
                "tool_call_count": len(snapshot.tool_calls),
                "stop_reason": snapshot.stop_reason,
            }
        return {
            "content": snapshot.content,
            "tool_calls": [
                asdict(tool_call)
                for tool_call in snapshot.tool_calls
            ],
        }

    def _set_usage_attributes(self, span: object, usage: ProviderUsage) -> None:
        """把 ProviderUsage 写入 span attributes。"""

        values = asdict(usage)
        if values.get("input_tokens") is not None:
            span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, values["input_tokens"])
        if values.get("output_tokens") is not None:
            span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, values["output_tokens"])
        if values.get("total_tokens") is not None:
            span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, values["total_tokens"])
        span.set_attribute(
            LANGFUSE_OBSERVATION_USAGE_DETAILS,
            json_attribute(values, policy=self._capture_policy),
        )


class InstrumentedProviderRequestBuilder:
    """在 provider request build boundary 上创建 span。"""

    def __init__(
        self,
        inner: ProviderRequestBuilder,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 builder 和观测配置。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    def build(self, context_runtime: object) -> ProviderRequest:
        """构造 provider request，并记录 provider.request.build span。"""

        with self._tracer.start_span(
            "provider.request.build",
            attributes={LANGFUSE_OBSERVATION_TYPE: "span"},
        ) as span:
            request = self._inner.build(context_runtime)  # type: ignore[arg-type]
            snapshot = build_provider_request_snapshot(request, self._capture_policy)
            span.set_attributes(
                {
                    "agentos.provider_request.system.length": snapshot.system_length,
                    "agentos.provider_request.messages.count": snapshot.message_count,
                    "agentos.provider_request.tools.count": snapshot.tool_count,
                    "agentos.provider_request.system.sha256": snapshot.system_sha256,
                    "agentos.provider_request.messages.sha256": snapshot.messages_sha256,
                    "agentos.provider_request.tools.sha256": snapshot.tools_sha256,
                },
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._request_payload(snapshot),
                    policy=self._capture_policy,
                ),
            )
            return request

    def _request_payload(self, snapshot: ProviderRequestSnapshot) -> dict[str, object]:
        """返回 request build span input payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                "system_length": snapshot.system_length,
                "system_sha256": snapshot.system_sha256,
                "message_count": snapshot.message_count,
                "messages_sha256": snapshot.messages_sha256,
                "tool_count": snapshot.tool_count,
                "tools_sha256": snapshot.tools_sha256,
            }
        return {
            "system": snapshot.system,
            "messages": snapshot.messages,
            "tools": snapshot.tools,
        }


class InstrumentedCompressionRuntime:
    """在 compression boundary 上创建 span。"""

    def __init__(
        self,
        inner: object,
        *,
        tracer: Tracer,
    ) -> None:
        """保存被包装 compression runtime。"""

        self._inner = inner
        self._tracer = tracer

    def maybe_compress(self) -> object:
        """执行压缩检查，并记录 compression span。"""

        with self._tracer.start_span(
            "compression.maybe_compress",
            attributes={LANGFUSE_OBSERVATION_TYPE: "span"},
        ) as span:
            result = self._inner.maybe_compress()
            span.set_attribute("agentos.compression.executed", result is not None)
            if result is not None and getattr(result, "id", None) is not None:
                span.set_attribute("agentos.compression.segment_id", result.id)
            return result


class InstrumentedToolCallRouter:
    """在 tool routing boundary 上创建 tool span。"""

    def __init__(
        self,
        inner: ToolCallRouter,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 router 和观测配置。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    def execute_tool_call(self, tool_call: ProviderToolCall) -> object:
        """执行 tool call，并记录 tool span。"""

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
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._tool_input_payload(call_snapshot),
                    policy=self._capture_policy,
                ),
            )
            result = self._inner.execute_tool_call(tool_call)
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
        """透传 provider tool schemas。"""

        return self._inner.tool_specs()

    def _tool_kind(self, tool_name: str) -> str:
        """推断 tool kind。"""

        if tool_name in CONTEXT_PROTOCOL_TOOL_NAMES:
            return "context"
        if tool_name.startswith("mcp__"):
            return "mcp"
        try:
            return self._inner.tool_registry.get(tool_name).kind
        except KeyError:
            return "unknown"

    def _tool_input_payload(self, snapshot: ToolCallSnapshot) -> dict[str, object]:
        """返回 tool span input payload。"""

        if self._capture_policy.mode == "metadata":
            return {"arguments_sha256": snapshot.arguments_sha256}
        return {"arguments": snapshot.arguments}

    def _tool_output_payload(self, snapshot: ToolResultSnapshot) -> dict[str, object]:
        """返回 tool span output payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                "content_length": snapshot.content_length,
                "content_sha256": snapshot.content_sha256,
            }
        return {"content": snapshot.content}


class InstrumentedQueryLoop:
    """在 QueryLoop turn boundary 上创建 root span。"""

    def __init__(
        self,
        inner: QueryLoop,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 QueryLoop 和观测配置。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    def run_turn(self, user_message: str) -> str:
        """运行 turn，并记录 agent.turn root span。"""

        attributes: dict[str, object] = {
            LANGFUSE_OBSERVATION_TYPE: "agent",
            LANGFUSE_TRACE_NAME: "agentos.turn",
            "agentos.capture.mode": self._capture_policy.mode,
            "agentos.turn.max_tool_iterations": self._inner.max_tool_iterations,
            "agentos.user_input.length": len(user_message),
        }
        if self._inner.session_state is not None:
            session_id = self._inner.session_state.id
            turn_id = f"turn_{self._inner.session_state.next_turn_number()}"
            attributes[LANGFUSE_SESSION_ID] = session_id
            attributes["agentos.session.id"] = session_id
            attributes["agentos.turn.id"] = turn_id

        with self._tracer.start_span("agent.turn", attributes=attributes) as span:
            input_payload = self._turn_input_payload(user_message)
            input_attribute = json_attribute(
                input_payload,
                policy=self._capture_policy,
            )
            span.set_attribute(LANGFUSE_TRACE_INPUT, input_attribute)
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT, input_attribute)
            response = self._inner.run_turn(user_message)
            span.set_attribute("agentos.final_response.length", len(response))
            output_attribute = json_attribute(
                self._turn_output_payload(response),
                policy=self._capture_policy,
            )
            span.set_attribute(LANGFUSE_TRACE_OUTPUT, output_attribute)
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, output_attribute)
            return response

    def build_request(self) -> ProviderRequest:
        """透传 build_request，供测试和高级调用者使用。"""

        return self._inner.build_request()

    def _turn_input_payload(self, user_message: str) -> dict[str, object]:
        """返回 root span input payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                "user_message_length": len(user_message),
                "user_message_sha256": stable_sha256(user_message),
            }
        return {"user_message": user_message}

    def _turn_output_payload(self, response: str) -> dict[str, object]:
        """返回 root span output payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                "content_length": len(response),
                "content_sha256": stable_sha256(response),
            }
        return {"content": response}
