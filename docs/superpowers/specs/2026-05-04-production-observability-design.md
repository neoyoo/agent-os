# Production Observability Design

## Goal

把 agentos 的 Phase 6 observability 从 import-free stub 升级为生产级观测架构：业务 runtime 不依赖 OpenTelemetry 或 Langfuse，但用户开启观测后，可以在 Langfuse 中看到一次 user request 触发的完整 LLM 交互、工具调用、压缩和错误链路。

这份 spec 只设计 observability。它不改变默认 LLM-visible context，不把 runtime metadata 渲染进 `ContextRenderer`，不把 hook 当日志系统使用。

## Design References

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/superpowers/specs/2026-05-03-phase-5-6-skills-mcp-persistence-observability-design.md`
- `../ai-knowledge/wiki/evaluation-observability.md`
- `../ai-knowledge/wiki/_patterns/otel-eval-bridge.md`
- `../ai-knowledge/wiki/hooks.md`
- `../ai-knowledge/wiki/query-loop.md`
- `../ai-knowledge/wiki/tool-system.md`
- `../ai-knowledge/wiki/prompt-system.md`
- `../ai-knowledge/projects/neoagent/decisions/2026-04-22-langfuse-integration.md`
- Langfuse OpenTelemetry integration docs
- Langfuse observation type docs
- OpenTelemetry GenAI semantic conventions

## Scope Contract

This belongs to Phase 6 production observability.

Acceptance items completed by this design:

- Real provider request/response observability through spans, including LLM input/output capture under policy.
- Langfuse generation/tool/agent observations through OTLP, not through ad hoc fake trace records.
- Optional OpenTelemetry dependency; core `agentos` imports and tests work without OTel installed.
- Clear separation between hooks, runtime events, debug projection and production tracing.
- Capture policy and redaction defaults that make local debugging useful without making production unsafe by default.
- Provider usage normalization so token/cost attribution can be attached to spans.

Intentionally deferred:

- Streaming token/chunk spans. The first production implementation records complete provider calls after response return.
- Cross-process trace propagation into MCP servers and future subagents. The first implementation records the client-side MCP/tool span and leaves W3C `traceparent` propagation for the multi-agent/MCP follow-up.
- Eval runner and in-memory exporter aggregation. The tracer protocol is designed to support it, but eval is a later phase.
- Langfuse prompt management, datasets, scores and prompt versioning. This spec covers runtime traces only.
- Cost pricing tables. This spec records token usage and optional provider-reported cost; price calculation can be layered later.

Design rule that must not be simplified away:

- Production observability cannot be implemented only by projecting `EventBus` records. `EventBus` does not carry complete provider input/output, nesting lifetime, exceptions around calls, or accurate span duration. It remains useful for append-only runtime facts and debug projection, but OTel spans must come from instrumentation around runtime boundaries.

## Current Problem

The current Phase 6 observability modules are deliberately lightweight:

- `EventLog` records typed runtime events.
- `EventTraceProjector` converts event records into normalized `TraceRecord` values.
- `OTelAdapter` and `LangfuseAdapter` output those trace records through injected clients.

That is acceptable for a stub, but it is not production LLM observability:

- `ProviderRequestBuiltEvent` and `ProviderResponseReceivedEvent` do not contain the rendered system prompt, active messages, provider tool schema, model, usage or response payload.
- Event projection creates disconnected trace records, not accurate nested spans with durations and exceptions.
- Langfuse cannot reliably display provider calls as `generation` observations without generation-specific attributes and input/output mapping.
- Hooks were previously tempting as log callbacks, but hooks are policy/interception points and should not become the observability pipeline.

The production design replaces "event projection as observability" with "proxy instrumentation as observability". Event projection remains a debug/export helper.

## Approach Options

### Option A: EventBus Projector Only

Keep the current model and enrich typed events with more payload.

Pros:

- Smallest code change.
- Works without optional dependencies.
- Event records are already persisted.

Cons:

- Pollutes runtime events with large prompt/response payloads.
- Cannot naturally represent span lifetime, nesting or exceptions.
- Encourages `QueryLoop` and other runtime code to emit observability metadata directly.
- Makes production privacy harder because event persistence would store the same payloads as traces.

Verdict: reject for production observability. Keep it only for debug projection and persisted runtime facts.

### Option B: Direct Langfuse SDK

Use Langfuse's Python SDK directly from the runtime or instrumentation layer.

Pros:

- Fastest Langfuse-specific integration.
- Easy access to Langfuse features beyond tracing.

Cons:

- Locks the SDK's production observability path to Langfuse.
- Core runtime or adapters become harder to test without Langfuse client objects.
- Jaeger, Tempo, SkyWalking and Datadog become second-class.

Verdict: reject as the default production architecture. It can be added later as an optional sink if needed.

### Option C: OTel Spans + Langfuse OTLP Backend

Use an agentos-owned tracer protocol and proxy instrumentation. The optional OTel implementation sends spans to any OTLP backend; Langfuse is the recommended default backend for LLM inspection.

Pros:

- Business code stays clean.
- Backend can be Langfuse today and Tempo/Jaeger/SkyWalking later.
- Langfuse can render `generation`, `tool` and `agent` observations from OTel span attributes.
- The same spans can later feed eval aggregation through an in-memory exporter.

Cons:

- More initial design and tests.
- Requires careful capture policy to avoid leaking prompts in production.

Verdict: choose this option.

## Boundary Model

### Hooks

Hooks are user intervention points:

- `before_provider_call`
- `after_provider_call`
- `before_tool_call`
- `after_tool_call`

They may allow, deny or modify execution through `HookResult`. They are not a logging API. Hook failures may be recorded by `HookManager` and may appear as span events later, but hooks do not produce traces.

### EventBus

`EventBus` publishes typed runtime facts:

- turn started/completed/failed
- messages appended
- provider request/response lifecycle facts
- tool execution facts
- context/compression/recall/persistence facts

Handlers are observation-only and must not mutate execution flow. `EventLog` persists these facts for session recovery and debug projection.

### Debug Projection

`context/debug_projection.py` is an explicit local inspection view. It may show runtime ids and event records. It is never called by `ProviderRequestBuilder`, and it never affects the default prompt.

### Production Observability

`observability/` owns traces, spans, capture policy, redaction and exporter setup. It observes runtime boundaries through wrappers. Runtime modules do not import OTel, Langfuse or span APIs.

## Production Architecture

```text
Application composition
  -> QueryLoop
  -> instrument_query_loop(loop, config)
  -> InstrumentedQueryLoop
       root span: agent.turn
       wraps:
         ProviderRequestBuilder
         Provider
         ToolCallRouter
         CompressionRuntime

Runtime execution
  -> agent.turn span
     -> compression.maybe_compress span
     -> provider.request.build span
     -> provider.complete generation span
     -> tool.<name> tool span
     -> provider.request.build span
     -> provider.complete generation span
```

Instrumentation is a construction-time decision:

- disabled: application uses raw `QueryLoop`.
- enabled: application wraps the loop through `instrument_query_loop(...)`.

`instrument_query_loop(...)` must not mutate the caller's original `QueryLoop`.
It creates a shallow configured `QueryLoop` copy whose provider, request builder,
tool router and compression runtime are replaced by instrumented proxies, then
returns an `InstrumentedQueryLoop` that owns the root `agent.turn` span and
delegates to that configured copy.

Business modules remain unaware of observability.

## Modules

Create or rewrite these modules:

- `src/agentos/observability/config.py`
  - `ObservabilityConfig`
  - `CapturePolicy`
  - `CaptureMode`
  - `Redactor`
  - default redaction helpers

- `src/agentos/observability/tracer.py`
  - agentos-owned `Tracer` Protocol
  - agentos-owned `Span` Protocol
  - `NoOpTracer`
  - `InMemoryTracer` for tests and eval follow-up

- `src/agentos/observability/snapshots.py`
  - `ProviderRequestSnapshot`
  - `ProviderResponseSnapshot`
  - `ToolCallSnapshot`
  - `ToolResultSnapshot`
  - safe serializers for Langfuse `input`/`output`

- `src/agentos/observability/conventions.py`
  - Langfuse attribute names
  - OpenTelemetry GenAI semantic convention names
  - agentos extension attribute names

- `src/agentos/observability/instrumented.py`
  - `InstrumentedQueryLoop`
  - `InstrumentedProviderRequestBuilder`
  - `InstrumentedProvider`
  - `InstrumentedToolCallRouter`
  - `InstrumentedCompressionRuntime`

- `src/agentos/observability/instrument.py`
  - `instrument_query_loop(loop, config) -> InstrumentedQueryLoop`
  - optional helpers for wrapping individual components in tests

- `src/agentos/observability/otel.py`
  - optional OTel implementation of the tracer protocol
  - `create_otel_tracer(...)`
  - `create_langfuse_otel_tracer(...)`
  - import-time failure message that tells users to install `agent-os[observability]`

- `src/agentos/observability/langfuse.py`
  - Langfuse OTLP endpoint and header helpers
  - no required Langfuse SDK dependency

Keep these modules, with narrower responsibility:

- `src/agentos/observability/events.py`
  - EventLog and EventRecord only.

- `src/agentos/observability/traces.py`
  - EventLog-to-debug-trace projection only.
  - It is not the production LLM trace pipeline.

## Public API

Expose from `agentos.observability`:

```python
from agentos.observability import (
    CaptureMode,
    CapturePolicy,
    InMemoryTracer,
    NoOpTracer,
    ObservabilityConfig,
    ProviderRequestSnapshot,
    ProviderResponseSnapshot,
    Redactor,
    Span,
    Tracer,
    create_langfuse_otel_tracer,
    create_otel_tracer,
    instrument_query_loop,
)
```

Public package imports must stay lowercase `agentos`.

## Tracer Protocol

The core SDK must not depend on OpenTelemetry types. The internal protocol is:

```python
class Span(Protocol):
    def set_attribute(self, key: str, value: object) -> None: ...
    def set_attributes(self, attributes: Mapping[str, object]) -> None: ...
    def add_event(self, name: str, attributes: Mapping[str, object] | None = None) -> None: ...
    def record_exception(self, error: BaseException) -> None: ...
    def set_status(self, status: str, description: str | None = None) -> None: ...
    def __enter__(self) -> "Span": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None: ...


class Tracer(Protocol):
    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Span: ...
```

The OTel implementation maps this to `start_as_current_span`. Test fakes can implement the same protocol without importing OTel.

## Capture Policy

Production defaults must be safe:

```python
@dataclass(frozen=True, slots=True)
class CapturePolicy:
    mode: CaptureMode = "metadata"
    capture_system: bool = False
    capture_messages: bool = False
    capture_tool_schemas: bool = False
    capture_provider_output: bool = False
    capture_tool_arguments: bool = False
    capture_tool_result: bool = False
    max_string_length: int = 4000
    redactor: Redactor = default_redactor
```

Modes:

- `metadata`: default. Records counts, lengths, hashes, ids, model, stop reason, tool names and usage. Does not record prompt, messages, tool args or tool results.
- `redacted`: records structured input/output after applying the configured redactor and length limit.
- `full`: records raw provider input/output and tool input/output, with only length limiting. Intended for local development against self-hosted Langfuse.

Convenience constructors:

- `CapturePolicy.metadata_only()`
- `CapturePolicy.redacted()`
- `CapturePolicy.full_for_local_development()`

Default redaction replaces likely secrets in strings:

- OpenAI/Anthropic/Langfuse style API keys.
- `Authorization`, `api_key`, `secret`, `token`, `password` object keys.
- PEM private key blocks.

The redactor runs before values are serialized into span attributes.

## Provider Usage

Add a normalized provider usage value:

```python
@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    cost_usd: float | None = None
```

Extend `ProviderResponse` with:

```python
usage: ProviderUsage | None = None
model: str | None = None
provider_name: str | None = None
response_id: str | None = None
```

Provider adapters should fill these fields when the provider response exposes them. The instrumentation layer must tolerate `None`.

## Snapshot Model

Provider request snapshot:

```python
@dataclass(frozen=True, slots=True)
class ProviderRequestSnapshot:
    system: str | None
    messages: tuple[dict[str, object], ...] | None
    tools: tuple[dict[str, object], ...] | None
    system_length: int
    message_count: int
    tool_count: int
    system_sha256: str
    messages_sha256: str
    tools_sha256: str
```

Provider response snapshot:

```python
@dataclass(frozen=True, slots=True)
class ProviderResponseSnapshot:
    content: str | None
    content_length: int
    content_sha256: str
    tool_calls: tuple[ToolCallSnapshot, ...]
    stop_reason: str | None
    usage: ProviderUsage | None
    model: str | None
    provider_name: str | None
    response_id: str | None
```

`None` content means the policy did not capture raw content. Lengths and hashes are still recorded for debugging and correlation.

Snapshot hashes are computed from canonical JSON with `sort_keys=True`,
`ensure_ascii=False` and compact separators. This keeps hashes stable across
equivalent dict ordering while preserving readable Chinese content when capture
is enabled.

## Span Hierarchy

For one turn with one tool call:

```text
agent.turn
├─ compression.maybe_compress
├─ provider.request.build
├─ provider.complete
├─ tool.read_file
├─ provider.request.build
└─ provider.complete
```

The root span starts in `InstrumentedQueryLoop.run_turn(...)` and ends when the turn succeeds or fails.

Nested spans are created by component proxies:

- `InstrumentedCompressionRuntime.maybe_compress(...)`
- `InstrumentedProviderRequestBuilder.build(...)`
- `InstrumentedProvider.complete(...)`
- `InstrumentedToolCallRouter.execute_tool_call(...)`

If an exception is raised, the current span records the exception and status `error`, then re-raises. Instrumentation must not swallow errors.

## Attribute Mapping

### Trace/Root Span

`agent.turn` attributes:

- `langfuse.observation.type = "agent"`
- `langfuse.trace.name = "agentos.turn"`
- `langfuse.session.id = session_id` when available
- `agentos.session.id = session_id` when available
- `agentos.turn.id = turn_id` when available
- `agentos.turn.max_tool_iterations`
- `agentos.capture.mode`

The root span input/output follows capture policy. In `metadata` mode it records only user input length/hash and final output length/hash.

### Provider Request Build Span

`provider.request.build` attributes:

- `langfuse.observation.type = "span"`
- `agentos.provider_request.system.length`
- `agentos.provider_request.messages.count`
- `agentos.provider_request.tools.count`
- `agentos.provider_request.system.sha256`
- `agentos.provider_request.messages.sha256`
- `agentos.provider_request.tools.sha256`

When policy allows capture:

- `langfuse.observation.input`
- `agentos.provider_request.system`
- `agentos.provider_request.messages`
- `agentos.provider_request.tools`

### Provider Complete Span

`provider.complete` attributes:

- `langfuse.observation.type = "generation"`
- `langfuse.observation.model.name`
- `gen_ai.operation.name = "chat"`
- `gen_ai.provider.name`
- `gen_ai.request.model`
- `gen_ai.response.finish_reasons`
- `gen_ai.usage.input_tokens`
- `gen_ai.usage.output_tokens`
- `gen_ai.usage.total_tokens`
- `agentos.provider.response_id`
- `agentos.provider.tool_call_count`

When policy allows capture:

- `langfuse.observation.input`
- `langfuse.observation.output`

For Langfuse compatibility, token usage is also serialized into `langfuse.observation.usage_details`.

### Tool Span

`tool.<name>` attributes:

- `langfuse.observation.type = "tool"`
- `tool.name`
- `tool.call_id`
- `agentos.tool.kind`
- `agentos.tool.arguments.sha256`
- `agentos.tool.result.sha256`
- `agentos.tool.result.length`

When policy allows capture:

- `langfuse.observation.input`
- `langfuse.observation.output`

The router wrapper must cover context protocol tools, external tools, skill tools and MCP tools because all four route through `ToolCallRouter`.

### Compression Span

`compression.maybe_compress` attributes:

- `langfuse.observation.type = "span"`
- `agentos.compression.executed`
- `agentos.compression.segment_id` when produced
- `agentos.compression.source_message_count` when known
- `agentos.compression.reason` when skipped

The first implementation may record only before/after metadata available from current return values and events. It must not record original message content.

## Langfuse OTLP Configuration

Langfuse is the recommended backend, but the SDK sends standard OTLP spans.

Factory:

```python
tracer = create_langfuse_otel_tracer(
    host="http://localhost:3000",
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
    service_name="agentos",
    environment="local",
)
```

The helper configures:

- OTLP HTTP trace endpoint: `{host}/api/public/otel/v1/traces`
- Authorization header: `Basic base64(public_key:secret_key)`
- Langfuse ingestion header: `x-langfuse-ingestion-version: 4`
- OTel resource attributes:
  - `service.name`
  - `deployment.environment.name`
  - `telemetry.sdk.language = python` when the OTel SDK provides it

The helper lives behind optional dependencies. Importing `agentos` or `agentos.observability` must not require OpenTelemetry packages unless the factory is called.

## Optional Dependencies

`pyproject.toml` adds:

```toml
[project.optional-dependencies]
observability = [
    "opentelemetry-api>=1.28",
    "opentelemetry-sdk>=1.28",
    "opentelemetry-exporter-otlp-proto-http>=1.28",
]
```

No required runtime dependency is added.

## User API

Local development with full capture:

```python
from agentos.observability import (
    CapturePolicy,
    ObservabilityConfig,
    create_langfuse_otel_tracer,
    instrument_query_loop,
)

tracer = create_langfuse_otel_tracer(
    host="http://localhost:3000",
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
    service_name="agentos-local",
    environment="local",
)

loop = instrument_query_loop(
    loop,
    ObservabilityConfig(
        tracer=tracer,
        capture_policy=CapturePolicy.full_for_local_development(),
    ),
)

loop.run_turn("帮我分析这个 bug")
```

Production with metadata-only capture:

```python
loop = instrument_query_loop(
    loop,
    ObservabilityConfig(
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    ),
)
```

Disabled:

```python
loop.run_turn("不启用观测时无需改业务代码")
```

## Implementation Rules

- `runtime/query_loop.py` must not import `agentos.observability`.
- `runtime/provider_request_builder.py` must not import `agentos.observability`.
- `providers/*` must not import OTel or Langfuse.
- `capabilities/router.py` must not import OTel or Langfuse.
- Observability wrappers may import runtime/provider/capability protocols.
- Instrumentation must preserve existing behavior and exception semantics.
- Instrumentation must not alter provider requests, provider responses, tool arguments or tool results.
- Capture policy must be applied before serializing anything to span attributes.
- Default prompt golden tests must continue to reject `session_id`, `trace_id`, `span_id`, `tool_call_id`, `schema_id`, `projection_id`, `compression_id`, `source` and `relevance`.

## Test Matrix

### Unit Tests

- `tests/observability/test_capture_policy.py`
  - metadata mode records lengths/hashes but not raw content.
  - redacted mode removes API keys and secret-like fields.
  - full mode captures raw provider request/response under length limit.

- `tests/observability/test_snapshots.py`
  - provider request snapshots are deterministic.
  - provider response snapshots include tool calls, stop reason and usage.
  - hashes are stable across equivalent dict ordering.

- `tests/observability/test_in_memory_tracer.py`
  - nested spans preserve parent/child order.
  - exceptions set error status and are re-raised by wrappers.

- `tests/observability/test_instrumented_provider.py`
  - provider span records `generation` type, model, stop reason and usage.
  - provider wrapper does not change `ProviderResponse`.

- `tests/observability/test_instrumented_router.py`
  - external, context, skill and MCP tool calls produce `tool` spans.
  - denied tool calls record error status and preserve the original exception.

- `tests/observability/test_otel_config.py`
  - Langfuse endpoint is `{host}/api/public/otel/v1/traces`.
  - Authorization is Basic `base64(public_key:secret_key)`.
  - `x-langfuse-ingestion-version` is set to `4`.
  - importing `agentos.observability` without OTel installed does not fail.

### Provider Adapter Tests

- `tests/providers/test_adapters.py`
  - OpenAI adapter maps prompt/completion/cached/reasoning usage when fields exist.
  - Anthropic adapter maps input/output/cache usage when fields exist.
  - missing usage remains `None` without breaking old fake clients.

### Integration Tests

- `tests/observability/test_query_loop_instrumentation.py`
  - a fake provider that calls one tool produces:

```text
agent.turn
├─ compression.maybe_compress
├─ provider.request.build
├─ provider.complete
├─ tool.<name>
├─ provider.request.build
└─ provider.complete
```

  - final assistant response is unchanged.
  - provider request span includes rendered system length/hash and tool count.
  - provider generation span includes assistant output length/hash and usage.

- `tests/context/test_renderer.py`
  - default context still omits runtime metadata after observability is installed.

- `tests/architecture/test_public_api.py`
  - public observability API names are exported.
  - mixed-case and snake-case package aliases are rejected.

### Smoke Test

Keep and update:

- `scripts/langfuse_otel_smoke_test.py`

Add a second smoke path after implementation:

- creates a fake `QueryLoop`
- instruments it with `create_langfuse_otel_tracer(...)`
- runs one turn
- prints the OTel trace id and Langfuse search hint

This script is not part of unit tests because it requires a running Langfuse instance and keys.

## Required Verification

```bash
uv run --python 3.11 --extra dev pytest -q
uv run --python 3.11 --extra dev python -m compileall -q src tests scripts
git diff --check
rg -n "agent[O]s|agent[_]os" src tests docs pyproject.toml README.md
rg -n "from opentelemetry|import opentelemetry|langfuse" src/agentos/runtime src/agentos/providers src/agentos/capabilities src/agentos/context
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance" tests/context/goldens src/agentos/context/renderer.py
```

The last command may report only tests that assert forbidden metadata is absent.

## Acceptance Checklist

| Requirement | Implementation files | Test files | Status |
|---|---|---|---|
| Observability enabled through construction-time instrumentation, not runtime imports. | `observability/instrument.py`, `observability/instrumented.py` | `tests/observability/test_query_loop_instrumentation.py` | required |
| OTel spans are produced for turn, provider request build, provider complete, tool calls and compression. | `observability/instrumented.py`, `observability/tracer.py` | `tests/observability/test_query_loop_instrumentation.py`, `tests/observability/test_in_memory_tracer.py` | required |
| Langfuse receives provider calls as generation observations through OTLP attributes. | `observability/conventions.py`, `observability/otel.py`, `observability/langfuse.py` | `tests/observability/test_instrumented_provider.py`, `tests/observability/test_otel_config.py` | required |
| Capture policy prevents raw prompt/message/tool data from being recorded by default. | `observability/config.py`, `observability/snapshots.py` | `tests/observability/test_capture_policy.py`, `tests/observability/test_snapshots.py` | required |
| Provider usage is normalized and attached to provider response spans. | `providers/base.py`, `providers/openai.py`, `providers/anthropic.py`, `providers/openai_compatible.py` | `tests/providers/test_adapters.py`, `tests/observability/test_instrumented_provider.py` | required |
| Tool spans cover external, context, skill and MCP routing paths. | `observability/instrumented.py`, `capabilities/router.py` boundary only | `tests/observability/test_instrumented_router.py` | required |
| EventBus remains typed facts/debug persistence, not the production trace source. | `observability/events.py`, `observability/traces.py` | `tests/observability/test_event_log.py`, `tests/observability/test_traces.py` | required |
| Hooks remain policy/interception points and do not become logging events. | `hooks/base.py`, `hooks/manager.py` | `tests/hooks/test_runtime.py`, `tests/observability/test_query_loop_instrumentation.py` | required |
| Core SDK imports and tests work without OTel installed. | `observability/__init__.py`, `observability/otel.py`, `pyproject.toml` | `tests/observability/test_otel_config.py`, full test suite | required |
| Default LLM-visible context stays metadata-free. | `context/renderer.py` unchanged by observability | `tests/context/test_renderer.py`, renderer golden tests | required |

The production observability task is complete only when every required row is implemented, tested and verified.
