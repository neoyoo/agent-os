# Trace Context Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production-grade trace context propagation to agentos observability, while keeping runtime metadata out of the default LLM context.

**Architecture:** OpenTelemetry remains the source of truth for `trace_id` and native span identity. agentos adds a small `ObservabilityContext` on `contextvars` for application identity such as `user_id` and incoming headers, extends the tracer boundary with extract/inject/current-id operations, and writes only necessary user/session/turn metadata to spans. The old EventLog-to-TraceRecord production-looking path is removed; EventLog and debug projection remain.

**Tech Stack:** Python 3.11 dataclasses, Protocols, `contextvars`, `contextlib`, stdlib regex/string parsing for the in-memory tracer, optional OpenTelemetry packages behind the `observability` extra, pytest.

---

## Scope And References

Read before editing:

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/superpowers/specs/2026-05-04-production-observability-design.md`
- `docs/superpowers/specs/2026-05-04-trace-context-propagation-design.md`
- `../ai-knowledge/wiki/evaluation-observability.md`
- `../ai-knowledge/wiki/_patterns/otel-eval-bridge.md`
- `../ai-knowledge/wiki/channel-remote.md`
- `../ai-knowledge/wiki/multi-agent.md`

Scope:

- Add `ObservabilityContext` and scoped helpers.
- Add tracer propagation protocol methods.
- Update `InMemoryTracer` and `_OTelTracer`.
- Write trace/session/user/turn metadata to root and child spans.
- Reduce metadata-mode Input/Output noise.
- Wire `AGENTOS_USER_ID` into the small OpenAI agent example.
- Remove old EventLog-to-TraceRecord public API and adapters.

Out of scope:

- Subagent implementation.
- HTTP/channel server.
- Automatic HTTP client instrumentation.
- Baggage-based automatic propagation of `user_id` or `session_id`.
- Langfuse Python SDK.

## File Structure

Create:

- `src/agentos/observability/context.py`: `ObservabilityContext`, current/scoped observability context, internal runtime trace context, public inject/current helpers.
- `src/agentos/observability/attributes.py`: common span metadata helpers and metadata-mode input/output summary helpers.
- `tests/observability/test_context.py`: contextvars behavior tests.
- `tests/observability/test_trace_propagation.py`: in-memory trace propagation tests.
- `tests/observability/test_otel_propagation.py`: optional OTel propagation tests that skip when OTel is unavailable.

Modify:

- `src/agentos/observability/tracer.py`: `TraceIds`, `TraceContextPropagator`, extended `Tracer`, `NoOpTracer`, `InMemoryTracer`.
- `src/agentos/observability/otel.py`: `_OTelTracer` propagation methods; remove `OTelAdapter`.
- `src/agentos/observability/langfuse.py`: keep OTLP helpers; remove `LangfuseAdapter`.
- `src/agentos/observability/conventions.py`: add `langfuse.user.id`, `user.id`, `session.id`, and `langfuse.trace.metadata.*` constants.
- `src/agentos/observability/config.py`: default tracer holder for public helper functions if needed.
- `src/agentos/observability/instrumented.py`: root/child metadata, metadata-mode input/output summaries.
- `src/agentos/observability/instrument.py`: set default tracer for public propagation helpers during instrumentation.
- `src/agentos/observability/__init__.py`: export new public API; remove old trace projection exports.
- `src/agentos/examples/small_openai_agent.py`: read `AGENTOS_USER_ID` and scope the turn with `use_observability_context`.
- `tests/observability/test_instrumented_provider.py`
- `tests/observability/test_instrumented_router.py`
- `tests/observability/test_query_loop_instrumentation.py`
- `tests/observability/test_otel_config.py`
- `tests/examples/test_small_openai_agent.py`
- `tests/architecture/test_public_api.py`

Delete:

- `src/agentos/observability/traces.py`
- `tests/observability/test_traces.py`

## Task 1: Observability Context API

**Files:**

- Create: `src/agentos/observability/context.py`
- Modify: `src/agentos/observability/__init__.py`
- Test: `tests/observability/test_context.py`
- Test: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing context tests**

Create `tests/observability/test_context.py`:

```python
from agentos.observability import (
    ObservabilityContext,
    current_observability_context,
    current_runtime_trace_context,
    use_observability_context,
    use_runtime_trace_context,
)


def test_observability_context_defaults_to_empty() -> None:
    context = current_observability_context()

    assert context == ObservabilityContext()
    assert context.user_id is None
    assert context.incoming_headers is None
    assert context.metadata == {}


def test_use_observability_context_sets_and_restores_values() -> None:
    incoming = {
        "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
    }

    with use_observability_context(
        user_id="u_1",
        incoming_headers=incoming,
        metadata={"channel": "cli"},
    ):
        context = current_observability_context()
        assert context.user_id == "u_1"
        assert context.incoming_headers == incoming
        assert context.metadata == {"channel": "cli"}

    assert current_observability_context() == ObservabilityContext()


def test_nested_observability_context_restores_outer_value() -> None:
    with use_observability_context(user_id="outer"):
        assert current_observability_context().user_id == "outer"
        with use_observability_context(user_id="inner"):
            assert current_observability_context().user_id == "inner"
        assert current_observability_context().user_id == "outer"


def test_runtime_trace_context_is_internal_and_scoped() -> None:
    assert current_runtime_trace_context().session_id is None
    assert current_runtime_trace_context().turn_id is None

    with use_runtime_trace_context(session_id="s1", turn_id="turn_1"):
        context = current_runtime_trace_context()
        assert context.session_id == "s1"
        assert context.turn_id == "turn_1"

    assert current_runtime_trace_context().session_id is None
    assert current_runtime_trace_context().turn_id is None
```

Update `tests/architecture/test_public_api.py` expected public names to include:

```python
expected = {
    "ObservabilityContext",
    "current_observability_context",
    "inject_trace_headers",
    "use_observability_context",
}
```

- [ ] **Step 2: Run context tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_context.py tests/architecture/test_public_api.py -q
```

Expected: fails with import errors for `ObservabilityContext` or the context helper functions.

- [ ] **Step 3: Implement context module and exports**

Create `src/agentos/observability/context.py`:

```python
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import ContextManager, Iterator, Mapping, MutableMapping

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


def current_trace_ids(
    tracer: TraceContextPropagator | None = None,
) -> TraceIds:
    """返回当前 active span 的 trace ids。"""

    propagator = tracer or _DEFAULT_TRACE_PROPAGATOR.get()
    return propagator.current_trace_ids()
```

Update `src/agentos/observability/__init__.py` to export:

```python
from agentos.observability.context import (
    ObservabilityContext,
    RuntimeTraceContext,
    current_observability_context,
    current_runtime_trace_context,
    current_trace_ids,
    inject_trace_headers,
    use_default_trace_propagator,
    use_observability_context,
    use_runtime_trace_context,
)
```

Add these names to `__all__`.

- [ ] **Step 4: Run context tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_context.py tests/architecture/test_public_api.py -q
```

Expected: context tests and public API tests pass. Task 7 handles removal of old trace projection public names.

- [ ] **Step 5: Commit context API**

Run:

```bash
git add src/agentos/observability/context.py src/agentos/observability/__init__.py tests/observability/test_context.py tests/architecture/test_public_api.py
git commit -m "feat: add observability context scope"
```

Expected: commit created.

## Task 2: Tracer Propagation Protocol And InMemoryTracer

**Files:**

- Modify: `src/agentos/observability/tracer.py`
- Modify: `src/agentos/observability/__init__.py`
- Test: `tests/observability/test_trace_propagation.py`
- Test: `tests/observability/test_in_memory_tracer.py`

- [ ] **Step 1: Write failing in-memory propagation tests**

Create `tests/observability/test_trace_propagation.py`:

```python
from agentos.observability import InMemoryTracer


def test_in_memory_tracer_creates_trace_id_for_root_span() -> None:
    tracer = InMemoryTracer()

    with tracer.start_span("agent.turn"):
        ids = tracer.current_trace_ids()

    assert ids.trace_id is not None
    assert len(ids.trace_id) == 32
    assert ids.span_id is not None
    assert len(ids.span_id) == 16
    assert ids.is_remote is False
    assert tracer.records[0].trace_id == ids.trace_id


def test_nested_spans_share_trace_id_and_have_distinct_span_ids() -> None:
    tracer = InMemoryTracer()

    with tracer.start_span("agent.turn"):
        root = tracer.current_trace_ids()
        with tracer.start_span("provider.complete"):
            child = tracer.current_trace_ids()

    assert child.trace_id == root.trace_id
    assert child.span_id != root.span_id
    assert tracer.records[1].parent_span_id == tracer.records[0].span_id


def test_in_memory_tracer_injects_current_traceparent() -> None:
    tracer = InMemoryTracer()
    headers: dict[str, str] = {}

    with tracer.start_span("agent.turn"):
        ids = tracer.current_trace_ids()
        tracer.inject_headers(headers)

    assert headers["traceparent"] == f"00-{ids.trace_id}-{ids.span_id}-01"


def test_in_memory_tracer_extracts_incoming_traceparent() -> None:
    tracer = InMemoryTracer()
    trace_id = "1" * 32
    parent_span_id = "2" * 16

    with tracer.use_incoming_headers(
        {"traceparent": f"00-{trace_id}-{parent_span_id}-01"},
    ):
        with tracer.start_span("agent.turn"):
            ids = tracer.current_trace_ids()

    assert ids.trace_id == trace_id
    assert tracer.records[0].trace_id == trace_id
    assert tracer.records[0].parent_span_id == parent_span_id


def test_current_trace_ids_is_empty_outside_span() -> None:
    tracer = InMemoryTracer()

    ids = tracer.current_trace_ids()

    assert ids.trace_id is None
    assert ids.span_id is None
```

Update `tests/observability/test_in_memory_tracer.py` assertions to account for `trace_id` on `InMemorySpanRecord`:

```python
assert tracer.records[0].trace_id == tracer.records[1].trace_id
```

- [ ] **Step 2: Run propagation tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_trace_propagation.py tests/observability/test_in_memory_tracer.py -q
```

Expected: fails because `TraceIds`, `inject_headers`, `use_incoming_headers`, or `trace_id` record fields do not exist.

- [ ] **Step 3: Implement tracer protocol and InMemoryTracer propagation**

Modify `src/agentos/observability/tracer.py`:

```python
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
from typing import ContextManager, Iterator, Mapping, MutableMapping, Protocol
from uuid import uuid4


_TRACEPARENT_PATTERN = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$",
)


@dataclass(frozen=True, slots=True)
class TraceIds:
    """当前 span 的 trace/span ids。"""

    trace_id: str | None
    span_id: str | None
    is_remote: bool = False


class TraceContextPropagator(Protocol):
    """trace context extract/inject 边界。"""

    def use_incoming_headers(
        self,
        headers: Mapping[str, str] | None,
    ) -> ContextManager[None]:
        """把 incoming headers 提取为当前 trace context。"""

    def inject_headers(self, headers: MutableMapping[str, str]) -> None:
        """把当前 trace context 写入 outgoing headers。"""

    def current_trace_ids(self) -> TraceIds:
        """返回当前 active span ids。"""
```

Extend `Tracer`:

```python
class Tracer(TraceContextPropagator, Protocol):
    """agentos 内部 tracer 边界。"""

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Span:
        """创建 span。"""
```

Extend `InMemorySpanRecord`:

```python
@dataclass(slots=True)
class InMemorySpanRecord:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    attributes: dict[str, object] = field(default_factory=dict)
    events: list[InMemorySpanEvent] = field(default_factory=list)
    status: str = "unset"
    status_description: str | None = None
```

Add methods to `NoOpTracer`:

```python
@contextmanager
def use_incoming_headers(
    self,
    headers: Mapping[str, str] | None,
) -> Iterator[None]:
    yield


def inject_headers(self, headers: MutableMapping[str, str]) -> None:
    """不写 headers。"""


def current_trace_ids(self) -> TraceIds:
    return TraceIds(trace_id=None, span_id=None)
```

Update `InMemoryTracer.__init__`:

```python
self._current_trace_id: ContextVar[str | None] = ContextVar(
    "agentos_current_trace_id",
    default=None,
)
self._current_span_id: ContextVar[str | None] = ContextVar(
    "agentos_current_span_id",
    default=None,
)
self._incoming_trace_id: ContextVar[str | None] = ContextVar(
    "agentos_incoming_trace_id",
    default=None,
)
self._incoming_parent_span_id: ContextVar[str | None] = ContextVar(
    "agentos_incoming_parent_span_id",
    default=None,
)
```

Implement `InMemoryTracer.start_span(...)`:

```python
trace_id = (
    self._current_trace_id.get()
    or self._incoming_trace_id.get()
    or uuid4().hex
)
parent_span_id = (
    self._current_span_id.get()
    or self._incoming_parent_span_id.get()
)
record = InMemorySpanRecord(
    name=name,
    trace_id=trace_id,
    span_id=uuid4().hex[:16],
    parent_span_id=parent_span_id,
    attributes=dict(attributes or {}),
)
```

Update `InMemorySpan.__enter__` to set both current trace id and current span id:

```python
self._trace_token = self._current_trace_id.set(self._record.trace_id)
self._span_token = self._current_span_id.set(self._record.span_id)
```

Update `InMemorySpan.__exit__` to reset both tokens.

Implement in-memory extract/inject:

```python
@contextmanager
def use_incoming_headers(
    self,
    headers: Mapping[str, str] | None,
) -> Iterator[None]:
    trace_id = None
    parent_span_id = None
    traceparent = None if headers is None else headers.get("traceparent")
    if traceparent is not None:
        match = _TRACEPARENT_PATTERN.match(traceparent)
        if match is not None:
            trace_id = match.group(1)
            parent_span_id = match.group(2)
    trace_token = self._incoming_trace_id.set(trace_id)
    parent_token = self._incoming_parent_span_id.set(parent_span_id)
    try:
        yield
    finally:
        self._incoming_trace_id.reset(trace_token)
        self._incoming_parent_span_id.reset(parent_token)


def inject_headers(self, headers: MutableMapping[str, str]) -> None:
    ids = self.current_trace_ids()
    if ids.trace_id is None or ids.span_id is None:
        return
    headers["traceparent"] = f"00-{ids.trace_id}-{ids.span_id}-01"


def current_trace_ids(self) -> TraceIds:
    return TraceIds(
        trace_id=self._current_trace_id.get(),
        span_id=self._current_span_id.get(),
    )
```

- [ ] **Step 4: Run propagation tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_trace_propagation.py tests/observability/test_in_memory_tracer.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit tracer propagation**

Run:

```bash
git add src/agentos/observability/tracer.py src/agentos/observability/__init__.py tests/observability/test_trace_propagation.py tests/observability/test_in_memory_tracer.py
git commit -m "feat: add trace context propagation protocol"
```

Expected: commit created.

## Task 3: OTel Propagation Implementation

**Files:**

- Modify: `src/agentos/observability/otel.py`
- Test: `tests/observability/test_otel_propagation.py`
- Test: `tests/observability/test_otel_config.py`

- [ ] **Step 1: Write failing optional OTel propagation tests**

Create `tests/observability/test_otel_propagation.py`:

```python
import pytest

pytest.importorskip("opentelemetry")

from agentos.observability.otel import _OTelTracer


def _test_otel_tracer() -> _OTelTracer:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    return _OTelTracer(provider.get_tracer("agentos-test"), provider)


def test_otel_tracer_injects_traceparent() -> None:
    tracer = _test_otel_tracer()
    headers: dict[str, str] = {}

    try:
        with tracer.start_span("agent.turn"):
            ids = tracer.current_trace_ids()
            tracer.inject_headers(headers)
    finally:
        tracer.shutdown()

    assert ids.trace_id is not None
    assert len(ids.trace_id) == 32
    assert "traceparent" in headers
    assert ids.trace_id in headers["traceparent"]


def test_otel_tracer_extracts_incoming_traceparent() -> None:
    trace_id = "1" * 32
    parent_span_id = "2" * 16
    tracer = _test_otel_tracer()

    try:
        with tracer.use_incoming_headers(
            {"traceparent": f"00-{trace_id}-{parent_span_id}-01"},
        ):
            with tracer.start_span("agent.turn"):
                ids = tracer.current_trace_ids()
    finally:
        tracer.shutdown()

    assert ids.trace_id == trace_id
```

The test uses OTel's in-memory exporter so it never contacts a local collector.

- [ ] **Step 2: Run OTel propagation tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra observability --extra dev pytest tests/observability/test_otel_propagation.py -q
```

Expected: fails because `_OTelTracer` does not implement `use_incoming_headers`, `inject_headers`, or `current_trace_ids`.

- [ ] **Step 3: Implement `_OTelTracer` propagation methods**

Modify `src/agentos/observability/otel.py`.

Add imports from agentos:

```python
from contextlib import contextmanager
from typing import Iterator, Mapping, MutableMapping

from agentos.observability.tracer import Span, TraceIds
```

Add methods to `_OTelTracer`:

```python
@contextmanager
def use_incoming_headers(
    self,
    headers: Mapping[str, str] | None,
) -> Iterator[None]:
    """提取 incoming W3C trace context。"""

    if not headers:
        yield
        return
    try:
        from opentelemetry import context, propagate
    except ImportError as error:
        raise RuntimeError(
            "OpenTelemetry is required. Install agent-os[observability].",
        ) from error
    extracted = propagate.extract(dict(headers))
    token = context.attach(extracted)
    try:
        yield
    finally:
        context.detach(token)


def inject_headers(self, headers: MutableMapping[str, str]) -> None:
    """把当前 OTel context 注入 outgoing headers。"""

    try:
        from opentelemetry import propagate
    except ImportError as error:
        raise RuntimeError(
            "OpenTelemetry is required. Install agent-os[observability].",
        ) from error
    propagate.inject(headers)


def current_trace_ids(self) -> TraceIds:
    """读取当前 OTel span context。"""

    try:
        from opentelemetry import trace
    except ImportError as error:
        raise RuntimeError(
            "OpenTelemetry is required. Install agent-os[observability].",
        ) from error
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return TraceIds(trace_id=None, span_id=None)
    return TraceIds(
        trace_id=format(span_context.trace_id, "032x"),
        span_id=format(span_context.span_id, "016x"),
        is_remote=getattr(span_context, "is_remote", False),
    )
```

Keep OpenTelemetry imports inside functions or methods. Do not import OpenTelemetry in `runtime/`, `providers/`, `capabilities/`, or `context/`.

- [ ] **Step 4: Run OTel propagation tests and config tests**

Run:

```bash
uv run --python 3.11 --extra observability --extra dev pytest tests/observability/test_otel_propagation.py tests/observability/test_otel_config.py -q
```

Expected: selected tests pass. With `--extra observability`, optional OTel packages are installed.

- [ ] **Step 5: Commit OTel propagation**

Run:

```bash
git add src/agentos/observability/otel.py tests/observability/test_otel_propagation.py tests/observability/test_otel_config.py
git commit -m "feat: support otel trace context propagation"
```

Expected: commit created.

## Task 4: Common Span Metadata And QueryLoop Context

**Files:**

- Create: `src/agentos/observability/attributes.py`
- Modify: `src/agentos/observability/conventions.py`
- Modify: `src/agentos/observability/instrumented.py`
- Modify: `src/agentos/observability/instrument.py`
- Test: `tests/observability/test_query_loop_instrumentation.py`

- [ ] **Step 1: Write failing query loop metadata tests**

Update `tests/observability/test_query_loop_instrumentation.py`:

```python
from agentos.observability import ObservabilityConfig, use_observability_context
```

Add tests:

```python
def test_query_loop_records_trace_session_turn_and_user_metadata_on_all_spans(tmp_path: Path) -> None:
    loop, _, _ = _build_loop(tmp_path)
    tracer = InMemoryTracer()
    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    with use_observability_context(user_id="u_1"):
        instrumented.run_turn("读取项目名")

    root_trace_id = tracer.records[0].attributes["agentos.trace.id"]
    for record in tracer.records:
        assert record.attributes["agentos.trace.id"] == root_trace_id
        assert record.attributes["agentos.session.id"] == "s1"
        assert record.attributes["agentos.turn.id"] == "turn_1"
        assert record.attributes["langfuse.session.id"] == "s1"
        assert record.attributes["session.id"] == "s1"
        assert record.attributes["langfuse.user.id"] == "u_1"
        assert record.attributes["user.id"] == "u_1"
        assert "agentos.user.id" not in record.attributes
        assert "agentos.span.id" not in record.attributes
        assert record.attributes["langfuse.trace.metadata.turn_id"] == "turn_1"
        assert record.attributes["langfuse.trace.metadata.capture_mode"] == "metadata"


def test_query_loop_inherits_incoming_traceparent(tmp_path: Path) -> None:
    loop, _, _ = _build_loop(tmp_path)
    tracer = InMemoryTracer()
    incoming_trace_id = "1" * 32
    instrumented = instrument_query_loop(
        loop,
        ObservabilityConfig(
            tracer=tracer,
            capture_policy=CapturePolicy.metadata_only(),
        ),
    )

    with use_observability_context(
        incoming_headers={
            "traceparent": f"00-{incoming_trace_id}-{'2' * 16}-01",
        },
    ):
        instrumented.run_turn("读取项目名")

    assert tracer.records[0].trace_id == incoming_trace_id
    assert tracer.records[0].attributes["agentos.trace.id"] == incoming_trace_id
    assert all(record.trace_id == incoming_trace_id for record in tracer.records)
```

- [ ] **Step 2: Run query loop metadata tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_query_loop_instrumentation.py -q
```

Expected: fails because common metadata attributes are missing from child spans.

- [ ] **Step 3: Implement conventions and common attributes**

Modify `src/agentos/observability/conventions.py`:

```python
LANGFUSE_USER_ID = "langfuse.user.id"
LANGFUSE_TRACE_METADATA_CAPTURE_MODE = "langfuse.trace.metadata.capture_mode"
LANGFUSE_TRACE_METADATA_TURN_ID = "langfuse.trace.metadata.turn_id"

USER_ID = "user.id"
SESSION_ID = "session.id"

AGENTOS_TRACE_ID = "agentos.trace.id"
AGENTOS_USER_ID = "agentos.user.id"
AGENTOS_SESSION_ID = "agentos.session.id"
AGENTOS_TURN_ID = "agentos.turn.id"
```

`AGENTOS_USER_ID` can exist as a constant for tests/searches, but instrumentation must not write it by default.

Create `src/agentos/observability/attributes.py`:

```python
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
    payload: dict[str, object] = {
        "capture_mode": capture_policy.mode,
        "content_hidden": True,
    }
    resolved_session_id = session_id or runtime_context.session_id
    resolved_turn_id = turn_id or runtime_context.turn_id
    if resolved_session_id is not None:
        payload["session_id"] = resolved_session_id
    if resolved_turn_id is not None:
        payload["turn_id"] = resolved_turn_id
    if observability_context.user_id is not None:
        payload["user_id"] = observability_context.user_id
    return payload
```

- [ ] **Step 4: Update InstrumentedQueryLoop**

Modify `src/agentos/observability/instrumented.py` imports:

```python
from agentos.observability.attributes import (
    apply_common_observability_attributes,
    metadata_identity_payload,
)
from agentos.observability.context import (
    current_observability_context,
    use_default_trace_propagator,
    use_runtime_trace_context,
)
```

Update `InstrumentedQueryLoop.run_turn(...)`:

```python
observability_context = current_observability_context()
session_id = None
turn_id = None
if self._inner.session_state is not None:
    session_id = self._inner.session_state.id
    turn_id = f"turn_{self._inner.session_state.next_turn_number()}"
if session_id is not None:
    attributes[LANGFUSE_SESSION_ID] = session_id
    attributes["agentos.session.id"] = session_id
if turn_id is not None:
    attributes["agentos.turn.id"] = turn_id

with use_default_trace_propagator(self._tracer):
    with self._tracer.use_incoming_headers(observability_context.incoming_headers):
        with use_runtime_trace_context(session_id=session_id, turn_id=turn_id):
            with self._tracer.start_span("agent.turn", attributes=attributes) as span:
                apply_common_observability_attributes(
                    span,
                    tracer=self._tracer,
                    capture_policy=self._capture_policy,
                    context=observability_context,
                    session_id=session_id,
                    turn_id=turn_id,
                )
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
```

Do not set `agentos.span.id`.

This enables `inject_trace_headers(headers)` to use the current instrumented tracer inside tools or future remote calls.

- [ ] **Step 5: Apply common metadata to child spans**

In `InstrumentedProvider`, `InstrumentedProviderRequestBuilder`, `InstrumentedToolCallRouter`, and `InstrumentedCompressionRuntime`, call:

```python
apply_common_observability_attributes(
    span,
    tracer=self._tracer,
    capture_policy=self._capture_policy,
)
```

`InstrumentedCompressionRuntime` currently has no capture policy. Update its constructor to accept `capture_policy: CapturePolicy`, pass it from `instrument_query_loop(...)`, and use it for common attributes.

Do not add OpenTelemetry imports outside `observability/otel.py`.

- [ ] **Step 6: Run query loop metadata tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_query_loop_instrumentation.py tests/observability/test_trace_propagation.py -q
```

Expected: selected tests pass.

- [ ] **Step 7: Commit common metadata**

Run:

```bash
git add src/agentos/observability/attributes.py src/agentos/observability/conventions.py src/agentos/observability/instrumented.py src/agentos/observability/instrument.py tests/observability/test_query_loop_instrumentation.py
git commit -m "feat: propagate trace metadata across spans"
```

Expected: commit created.

## Task 5: Metadata Mode Input/Output Cleanup

**Files:**

- Modify: `src/agentos/observability/instrumented.py`
- Test: `tests/observability/test_instrumented_provider.py`
- Test: `tests/observability/test_instrumented_router.py`
- Test: `tests/observability/test_query_loop_instrumentation.py`

- [ ] **Step 1: Write failing metadata cleanup tests**

Update `tests/observability/test_instrumented_provider.py` metadata test:

```python
def test_instrumented_provider_metadata_mode_records_readable_summary_without_hashes() -> None:
    tracer = InMemoryTracer()
    provider = RecordingProvider(ProviderResponse(content="done", stop_reason="stop"))
    instrumented = InstrumentedProvider(
        provider,
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )

    instrumented.complete(
        ProviderRequest(
            system="system text",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
        ),
    )

    span = tracer.records[0]
    input_attribute = str(span.attributes["langfuse.observation.input"])
    output_attribute = str(span.attributes["langfuse.observation.output"])
    assert "system_chars" in input_attribute
    assert "message_count" in input_attribute
    assert "tool_count" in input_attribute
    assert "sha256" not in input_attribute
    assert "system text" not in input_attribute
    assert "content_chars" in output_attribute
    assert "tool_call_count" in output_attribute
    assert "sha256" not in output_attribute
    assert "done" not in output_attribute
    assert "agentos.provider_request.system.sha256" in span.attributes
```

Update router metadata test:

```python
assert "arguments_hidden" in str(span.attributes["langfuse.observation.input"])
assert "sha256" not in str(span.attributes["langfuse.observation.input"])
assert "content_chars" in str(span.attributes["langfuse.observation.output"])
assert "sha256" not in str(span.attributes["langfuse.observation.output"])
assert "agentos.tool.arguments.sha256" in span.attributes
```

Update query loop metadata test:

```python
root_input = str(root.attributes["langfuse.trace.input"])
root_output = str(root.attributes["langfuse.trace.output"])
assert "user_message_chars" in root_input
assert "sha256" not in root_input
assert "content_chars" in root_output
assert "sha256" not in root_output
```

- [ ] **Step 2: Run metadata cleanup tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_instrumented_provider.py tests/observability/test_instrumented_router.py tests/observability/test_query_loop_instrumentation.py -q
```

Expected: fails because metadata Input/Output still include sha256 fields.

- [ ] **Step 3: Update metadata payload helpers**

In `src/agentos/observability/instrumented.py`, update metadata branches.

Provider input:

```python
if self._capture_policy.mode == "metadata":
    return {
        **metadata_identity_payload(capture_policy=self._capture_policy),
        "system_chars": snapshot.system_length,
        "message_count": snapshot.message_count,
        "tool_count": snapshot.tool_count,
    }
```

Provider output:

```python
if self._capture_policy.mode == "metadata":
    return {
        **metadata_identity_payload(capture_policy=self._capture_policy),
        "content_chars": snapshot.content_length,
        "tool_call_count": len(snapshot.tool_calls),
        "stop_reason": snapshot.stop_reason,
    }
```

Provider request builder input:

```python
if self._capture_policy.mode == "metadata":
    return {
        **metadata_identity_payload(capture_policy=self._capture_policy),
        "system_chars": snapshot.system_length,
        "message_count": snapshot.message_count,
        "tool_count": snapshot.tool_count,
    }
```

Tool input:

```python
if self._capture_policy.mode == "metadata":
    return {
        **metadata_identity_payload(capture_policy=self._capture_policy),
        "arguments_hidden": True,
    }
```

Tool output:

```python
if self._capture_policy.mode == "metadata":
    return {
        **metadata_identity_payload(capture_policy=self._capture_policy),
        "content_chars": snapshot.content_length,
    }
```

Root input:

```python
if self._capture_policy.mode == "metadata":
    return {
        **metadata_identity_payload(capture_policy=self._capture_policy),
        "user_message_chars": len(user_message),
    }
```

Root output:

```python
if self._capture_policy.mode == "metadata":
    return {
        **metadata_identity_payload(capture_policy=self._capture_policy),
        "content_chars": len(response),
    }
```

Keep sha256 attributes already set on spans. Do not remove `agentos.provider_request.*.sha256` or `agentos.tool.*.sha256`.

- [ ] **Step 4: Run metadata cleanup tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_instrumented_provider.py tests/observability/test_instrumented_router.py tests/observability/test_query_loop_instrumentation.py -q
```

Expected: selected tests pass.

- [ ] **Step 5: Commit metadata cleanup**

Run:

```bash
git add src/agentos/observability/instrumented.py tests/observability/test_instrumented_provider.py tests/observability/test_instrumented_router.py tests/observability/test_query_loop_instrumentation.py
git commit -m "fix: reduce metadata capture noise"
```

Expected: commit created.

## Task 6: Small Agent User Context

**Files:**

- Modify: `src/agentos/examples/small_openai_agent.py`
- Modify: `tests/examples/test_small_openai_agent.py`

- [ ] **Step 1: Write failing small agent user id test**

Update `tests/examples/test_small_openai_agent.py`:

```python
def test_main_observe_langfuse_uses_agentos_user_id(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    tracer = InMemoryTracer()
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("AGENTOS_OBSERVABILITY_CAPTURE", "metadata")
    monkeypatch.setenv("AGENTOS_USER_ID", "local_user")
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.create_langfuse_otel_tracer",
        lambda **kwargs: tracer,
    )

    exit_code = main(["--observe-langfuse", "hello"])

    assert exit_code == 0
    capsys.readouterr()
    for record in tracer.records:
        assert record.attributes["langfuse.user.id"] == "local_user"
        assert record.attributes["user.id"] == "local_user"
        assert "agentos.user.id" not in record.attributes
```

- [ ] **Step 2: Run small agent tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/examples/test_small_openai_agent.py -q
```

Expected: fails because `AGENTOS_USER_ID` is not scoped into `ObservabilityContext`.

- [ ] **Step 3: Implement small agent user context**

Modify `src/agentos/examples/small_openai_agent.py` imports:

```python
from agentos.observability import (
    CapturePolicy,
    ObservabilityConfig,
    create_langfuse_otel_tracer,
    instrument_query_loop,
    use_observability_context,
)
```

Update `main(...)` final execution:

```python
user_id = os.environ.get("AGENTOS_USER_ID")
with use_observability_context(user_id=user_id or None):
    print(loop.run_turn(user_message))
return 0
```

Do not add a new CLI flag in this task. Formal user identity wiring for HTTP/channel belongs to a future channel spec.

- [ ] **Step 4: Run small agent tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/examples/test_small_openai_agent.py -q
```

Expected: selected tests pass.

- [ ] **Step 5: Commit small agent user context**

Run:

```bash
git add src/agentos/examples/small_openai_agent.py tests/examples/test_small_openai_agent.py
git commit -m "feat: pass user id into observability context"
```

Expected: commit created.

## Task 7: Remove Deprecated Event-To-Trace Production Path

**Files:**

- Delete: `src/agentos/observability/traces.py`
- Delete: `tests/observability/test_traces.py`
- Modify: `src/agentos/observability/langfuse.py`
- Modify: `src/agentos/observability/otel.py`
- Modify: `src/agentos/observability/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Test: `tests/observability/test_event_log.py`
- Test: `tests/context/test_debug_projection.py`
- Test: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing public API cleanup test**

Update `tests/architecture/test_public_api.py`.

Ensure these names are present:

```python
expected = {
    "CapturePolicy",
    "ObservabilityConfig",
    "ObservabilityContext",
    "InMemoryTracer",
    "NoOpTracer",
    "instrument_query_loop",
    "create_otel_tracer",
    "create_langfuse_otel_tracer",
    "inject_trace_headers",
    "use_observability_context",
}
```

Ensure these names are absent:

```python
for removed_name in [
    "TraceRecord",
    "TraceSink",
    "EventTraceProjector",
    "OTelAdapter",
    "LangfuseAdapter",
]:
    assert not hasattr(agentos.observability, removed_name)
```

- [ ] **Step 2: Run public API cleanup tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/architecture/test_public_api.py tests/observability/test_event_log.py tests/context/test_debug_projection.py -q
```

Expected: public API test fails because old trace projection names are still exported.

- [ ] **Step 3: Remove old trace projection modules and exports**

Delete both files with `apply_patch` delete hunks:

```text
*** Begin Patch
*** Delete File: src/agentos/observability/traces.py
*** Delete File: tests/observability/test_traces.py
*** End Patch
```

Modify `src/agentos/observability/langfuse.py` to keep only:

```python
import base64


def langfuse_otel_trace_endpoint(host: str) -> str:
    """返回 Langfuse OTLP HTTP trace endpoint。"""

    return f"{host.rstrip('/')}/api/public/otel/v1/traces"


def langfuse_otel_headers(public_key: str, secret_key: str) -> dict[str, str]:
    """返回 Langfuse OTLP 需要的认证 headers。"""

    auth = base64.b64encode(
        f"{public_key}:{secret_key}".encode("utf-8"),
    ).decode("ascii")
    return {
        "Authorization": f"Basic {auth}",
        "x-langfuse-ingestion-version": "4",
    }
```

Modify `src/agentos/observability/otel.py`:

- Remove `from agentos.observability.traces import TraceRecord`.
- Delete `class OTelAdapter`.
- Keep `_OTelSpan`, `_OTelTracer`, `create_otel_tracer`, and `create_langfuse_otel_tracer`.

Modify `src/agentos/observability/__init__.py`:

- Remove imports and `__all__` entries for `TraceRecord`, `TraceSink`, `EventTraceProjector`, `OTelAdapter`, `LangfuseAdapter`.
- Keep `EventLog`, `EventRecord`, and event serialization exports.

- [ ] **Step 4: Run cleanup tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/architecture/test_public_api.py tests/observability/test_event_log.py tests/context/test_debug_projection.py -q
```

Expected: selected tests pass. `EventLog` and debug projection still work.

- [ ] **Step 5: Search for stale imports**

Run:

```bash
rg -n "TraceRecord|TraceSink|EventTraceProjector|OTelAdapter|LangfuseAdapter|observability\\.traces" src tests docs
```

Expected: matches are limited to historical design docs and this implementation plan. There must be no matches in `src/` or active tests other than explicit removed-name assertions.

- [ ] **Step 6: Commit cleanup**

Run:

```bash
git add -A src/agentos/observability tests/architecture/test_public_api.py
git commit -m "refactor: remove event trace projection adapters"
```

Expected: commit created.

## Task 8: Full Verification

**Files:** all changed files.

- [ ] **Step 1: Run observability tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability tests/examples/test_small_openai_agent.py tests/architecture/test_public_api.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run optional OTel tests**

Run:

```bash
uv run --python 3.11 --extra observability --extra dev pytest tests/observability/test_otel_propagation.py tests/observability/test_otel_config.py -q
```

Expected: selected OTel tests pass.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run --python 3.11 --extra dev pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run compileall**

Run:

```bash
uv run --python 3.11 --extra dev python -m compileall -q src tests scripts
```

Expected: exit 0.

- [ ] **Step 5: Run whitespace and architecture drift checks**

Run:

```bash
git diff --check
rg -n "agent[O]s|agent[_]os" src tests docs pyproject.toml AGENTS.md .gitignore
rg -n "from opentelemetry|import opentelemetry|langfuse" src/agentos/runtime src/agentos/providers src/agentos/capabilities src/agentos/context
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance" tests/context/goldens src/agentos/context/renderer.py
```

Expected:

- `git diff --check` exits 0.
- old-name search has no matches.
- forbidden OTel/Langfuse import search has no matches.
- default renderer metadata search has no forbidden prompt matches.

- [ ] **Step 6: Final status review**

Review the spec acceptance checklist and map each requirement to implementation and tests:

```text
Trace id source: tracer.py / otel.py / test_trace_propagation.py
Incoming extract: tracer.py / otel.py / test_trace_propagation.py / test_otel_propagation.py
Outgoing inject: context.py / tracer.py / otel.py / test_trace_propagation.py
Span metadata: attributes.py / instrumented.py / test_query_loop_instrumentation.py
Metadata cleanup: instrumented.py / test_instrumented_provider.py
Deprecated cleanup: langfuse.py / otel.py / __init__.py / test_public_api.py
Prompt safety: renderer drift search / context renderer tests
```

Expected: no requirement remains deferred.

- [ ] **Step 7: Commit verification-only updates if any**

When verification required small doc/test adjustments, commit them:

```bash
git add src tests docs
git commit -m "test: verify trace context propagation"
```

Expected: only needed if previous tasks left uncommitted verification changes.
