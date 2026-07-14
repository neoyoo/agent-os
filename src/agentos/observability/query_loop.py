from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from agentos.observability.attributes import (
    apply_common_observability_attributes,
    metadata_identity_payload,
)
from agentos.observability.config import CapturePolicy, json_attribute
from agentos.observability.context import (
    ObservabilityContext,
    current_observability_context,
    use_default_trace_propagator,
    use_runtime_trace_context,
)
from agentos.observability.conventions import (
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
    LANGFUSE_TRACE_INPUT,
    LANGFUSE_TRACE_NAME,
    LANGFUSE_TRACE_OUTPUT,
)
from agentos.observability.snapshots import ProviderRequestSnapshot
from agentos.observability.tracer import Span, Tracer
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.run import RunRequest, UserTurnInput
from agentos.runtime.stream_events import TurnStreamCompleted, TurnStreamEvent


class _SessionStateBoundary(Protocol):
    id: str

    def next_turn_number(self) -> int: ...


class _RequestBuilderBoundary(Protocol):
    latest_request_snapshot: ProviderRequestSnapshot | None


class QueryLoopBoundary(Protocol):
    max_tool_iterations: int
    request_builder: _RequestBuilderBoundary
    session_state: _SessionStateBoundary | None

    async def execute(self, request: RunRequest) -> AgentStream: ...

    def interrupt(self) -> bool: ...

    def _wait_until_idle(self) -> None: ...


class InstrumentedQueryLoop:
    """在唯一 QueryLoop execute 边界上附加 root span。"""

    def __init__(
        self,
        inner: QueryLoopBoundary,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    async def execute(self, request: RunRequest) -> AgentStream:
        user_message = (
            request.input.content if isinstance(request.input, UserTurnInput) else ""
        )
        observability_context = current_observability_context()
        session_id, turn_id = self._turn_identity()
        stream = await self._inner.execute(request)
        stream._transform_events(
            lambda events: self._trace_events(
                events,
                user_message=user_message,
                observability_context=observability_context,
                session_id=session_id,
                turn_id=turn_id,
            ),
        )
        return stream

    @property
    def request_builder(self) -> _RequestBuilderBoundary:
        return self._inner.request_builder

    def interrupt(self) -> bool:
        return self._inner.interrupt()

    def _wait_until_idle(self) -> None:
        self._inner._wait_until_idle()

    async def _trace_events(
        self,
        events: AsyncIterator[TurnStreamEvent],
        *,
        user_message: str,
        observability_context: ObservabilityContext,
        session_id: str | None,
        turn_id: str | None,
    ) -> AsyncIterator[TurnStreamEvent]:
        attributes: dict[str, object] = {
            LANGFUSE_OBSERVATION_TYPE: "agent",
            LANGFUSE_TRACE_NAME: "agentos.turn",
            "agentos.capture.mode": self._capture_policy.mode,
            "agentos.turn.max_tool_iterations": self._inner.max_tool_iterations,
            "agentos.user_input.length": len(user_message),
        }
        if session_id is not None:
            attributes[LANGFUSE_SESSION_ID] = session_id
            attributes["agentos.session.id"] = session_id
        if turn_id is not None:
            attributes["agentos.turn.id"] = turn_id

        with use_default_trace_propagator(self._tracer):
            with self._tracer.use_incoming_headers(
                observability_context.incoming_headers,
            ):
                with use_runtime_trace_context(
                    session_id=session_id,
                    turn_id=turn_id,
                ):
                    with self._tracer.start_span(
                        "agent.turn",
                        attributes=attributes,
                    ) as span:
                        self._initialize_span(
                            span,
                            user_message=user_message,
                            observability_context=observability_context,
                            session_id=session_id,
                            turn_id=turn_id,
                        )
                        try:
                            async for event in events:
                                if isinstance(event, TurnStreamCompleted):
                                    self._record_output(span, event.content)
                                yield event
                        finally:
                            self._refresh_turn_input_attributes(span, user_message)

    def _initialize_span(
        self,
        span: Span,
        *,
        user_message: str,
        observability_context: ObservabilityContext,
        session_id: str | None,
        turn_id: str | None,
    ) -> None:
        apply_common_observability_attributes(
            span,
            tracer=self._tracer,
            capture_policy=self._capture_policy,
            context=observability_context,
            session_id=session_id,
            turn_id=turn_id,
        )
        input_attribute = json_attribute(
            self._turn_input_payload(user_message),
            policy=self._capture_policy,
        )
        span.set_attribute(LANGFUSE_TRACE_INPUT, input_attribute)
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT, input_attribute)

    def _record_output(self, span: Span, response: str) -> None:
        span.set_attribute("agentos.final_response.length", len(response))
        output_attribute = json_attribute(
            self._turn_output_payload(response),
            policy=self._capture_policy,
        )
        span.set_attribute(LANGFUSE_TRACE_OUTPUT, output_attribute)
        span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, output_attribute)

    def _turn_identity(self) -> tuple[str | None, str | None]:
        session = self._inner.session_state
        if session is None:
            return None, None
        return session.id, f"turn_{session.next_turn_number()}"

    def _turn_input_payload(self, user_message: str) -> dict[str, object]:
        if self._capture_policy.mode == "metadata":
            return {
                **metadata_identity_payload(capture_policy=self._capture_policy),
                "user_message_chars": len(user_message),
            }
        payload: dict[str, object] = {"user_message": user_message}
        snapshot = self._inner.request_builder.latest_request_snapshot
        if snapshot is not None:
            payload["latest_provider_request"] = {
                "system": snapshot.system,
                "messages": snapshot.messages,
                "tools": snapshot.tools,
            }
        return payload

    def _refresh_turn_input_attributes(self, span: Span, user_message: str) -> None:
        if self._capture_policy.mode == "metadata":
            return
        input_attribute = json_attribute(
            self._turn_input_payload(user_message),
            policy=self._capture_policy,
        )
        span.set_attribute(LANGFUSE_TRACE_INPUT, input_attribute)
        span.set_attribute(LANGFUSE_OBSERVATION_INPUT, input_attribute)

    def _turn_output_payload(self, response: str) -> dict[str, object]:
        if self._capture_policy.mode == "metadata":
            return {
                **metadata_identity_payload(capture_policy=self._capture_policy),
                "content_chars": len(response),
            }
        return {"content": response}
