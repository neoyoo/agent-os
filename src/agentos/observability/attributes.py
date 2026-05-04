from __future__ import annotations

from agentos.observability.config import CapturePolicy
from agentos.observability.context import (
    ObservabilityContext,
    current_observability_context,
    current_runtime_trace_context,
)
from agentos.observability.conventions import (
    AGENTOS_SESSION_ID,
    AGENTOS_TRACE_ID,
    AGENTOS_TURN_ID,
    LANGFUSE_SESSION_ID,
    LANGFUSE_TRACE_METADATA_CAPTURE_MODE,
    LANGFUSE_TRACE_METADATA_TURN_ID,
    LANGFUSE_USER_ID,
    SESSION_ID,
    USER_ID,
)
from agentos.observability.tracer import Span, Tracer


def apply_common_observability_attributes(
    span: Span,
    *,
    tracer: Tracer,
    capture_policy: CapturePolicy,
    context: ObservabilityContext | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, object]:
    """写入所有 span 共享的低噪声观测 attributes。"""

    observability_context = context or current_observability_context()
    runtime_context = current_runtime_trace_context()
    resolved_session_id = session_id or runtime_context.session_id
    resolved_turn_id = turn_id or runtime_context.turn_id
    attributes: dict[str, object] = {
        LANGFUSE_TRACE_METADATA_CAPTURE_MODE: capture_policy.mode,
    }
    trace_ids = tracer.current_trace_ids()
    if trace_ids.trace_id is not None:
        attributes[AGENTOS_TRACE_ID] = trace_ids.trace_id
    if observability_context.user_id is not None:
        attributes[LANGFUSE_USER_ID] = observability_context.user_id
        attributes[USER_ID] = observability_context.user_id
    if resolved_session_id is not None:
        attributes[LANGFUSE_SESSION_ID] = resolved_session_id
        attributes[SESSION_ID] = resolved_session_id
        attributes[AGENTOS_SESSION_ID] = resolved_session_id
    if resolved_turn_id is not None:
        attributes[AGENTOS_TURN_ID] = resolved_turn_id
        attributes[LANGFUSE_TRACE_METADATA_TURN_ID] = resolved_turn_id
    for key, value in observability_context.metadata.items():
        attributes[f"langfuse.trace.metadata.{key}"] = value
    span.set_attributes(attributes)
    return attributes


def metadata_identity_payload(
    *,
    capture_policy: CapturePolicy,
    context: ObservabilityContext | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, object]:
    """返回 metadata mode Input/Output 的公共摘要字段。"""

    observability_context = context or current_observability_context()
    runtime_context = current_runtime_trace_context()
    resolved_session_id = session_id or runtime_context.session_id
    resolved_turn_id = turn_id or runtime_context.turn_id
    payload: dict[str, object] = {
        "capture_mode": capture_policy.mode,
        "content_hidden": True,
    }
    if resolved_session_id is not None:
        payload["session_id"] = resolved_session_id
    if resolved_turn_id is not None:
        payload["turn_id"] = resolved_turn_id
    if observability_context.user_id is not None:
        payload["user_id"] = observability_context.user_id
    return payload
