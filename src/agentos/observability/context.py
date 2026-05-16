from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Mapping, MutableMapping

from agentos.observability.tracer import NoOpTracer, TraceContextPropagator, TraceIds


@dataclass(frozen=True, slots=True)
class ObservabilityContext:
    """当前调用链上的观测上下文，不进入 LLM prompt。"""

    user_id: str | None = None
    incoming_headers: Mapping[str, str] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeTraceContext:
    """observability 内部使用的 runtime 关联上下文。"""

    session_id: str | None = None
    turn_id: str | None = None


_CURRENT_OBSERVABILITY_CONTEXT: ContextVar[ObservabilityContext] = ContextVar(
    "agentos_observability_context",
    default=ObservabilityContext(),
)
_CURRENT_RUNTIME_TRACE_CONTEXT: ContextVar[RuntimeTraceContext] = ContextVar(
    "agentos_runtime_trace_context",
    default=RuntimeTraceContext(),
)
_DEFAULT_TRACE_PROPAGATOR: ContextVar[TraceContextPropagator] = ContextVar(
    "agentos_default_trace_propagator",
    default=NoOpTracer(),
)


def current_observability_context() -> ObservabilityContext:
    """返回当前作用域的观测上下文。"""

    return _CURRENT_OBSERVABILITY_CONTEXT.get()


@contextmanager
def use_observability_context(
    context: ObservabilityContext | None = None,
    *,
    user_id: str | None = None,
    incoming_headers: Mapping[str, str] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> Iterator[ObservabilityContext]:
    """在当前作用域设置观测上下文。"""

    next_context = context or ObservabilityContext(
        user_id=user_id,
        incoming_headers=incoming_headers,
        metadata=dict(metadata or {}),
    )
    token = _CURRENT_OBSERVABILITY_CONTEXT.set(next_context)
    try:
        yield next_context
    finally:
        _CURRENT_OBSERVABILITY_CONTEXT.reset(token)


def current_runtime_trace_context() -> RuntimeTraceContext:
    """返回当前 observability runtime trace context。"""

    return _CURRENT_RUNTIME_TRACE_CONTEXT.get()


@contextmanager
def use_runtime_trace_context(
    *,
    session_id: str | None,
    turn_id: str | None,
) -> Iterator[RuntimeTraceContext]:
    """在当前作用域设置 runtime trace context。"""

    context = RuntimeTraceContext(session_id=session_id, turn_id=turn_id)
    token = _CURRENT_RUNTIME_TRACE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_RUNTIME_TRACE_CONTEXT.reset(token)


@contextmanager
def use_default_trace_propagator(
    tracer: TraceContextPropagator,
) -> Iterator[TraceContextPropagator]:
    """设置 public propagation helpers 默认使用的 tracer。"""

    token = _DEFAULT_TRACE_PROPAGATOR.set(tracer)
    try:
        yield tracer
    finally:
        _DEFAULT_TRACE_PROPAGATOR.reset(token)


def inject_trace_headers(
    headers: MutableMapping[str, str],
    tracer: TraceContextPropagator | None = None,
) -> MutableMapping[str, str]:
    """把当前 trace context 注入 outgoing headers。"""

    propagator = tracer or _DEFAULT_TRACE_PROPAGATOR.get()
    propagator.inject_headers(headers)
    return headers


@contextmanager
def use_incoming_trace_headers(
    headers: Mapping[str, str] | None,
    tracer: TraceContextPropagator | None = None,
) -> Iterator[None]:
    """把 incoming trace headers 应用到当前作用域。"""

    propagator = tracer or _DEFAULT_TRACE_PROPAGATOR.get()
    with propagator.use_incoming_headers(headers):
        yield


def current_trace_ids(
    tracer: TraceContextPropagator | None = None,
) -> TraceIds:
    """返回当前 active span 的 trace ids。"""

    propagator = tracer or _DEFAULT_TRACE_PROPAGATOR.get()
    return propagator.current_trace_ids()
