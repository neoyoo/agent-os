# Production Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement production-grade OTel span instrumentation for agentos and wire it into the small OpenAI-compatible agent example.

**Architecture:** Observability is enabled at composition time through `instrument_query_loop(...)`. Core runtime, providers, context and tool routing do not import OpenTelemetry or Langfuse; observability proxies create spans around provider request build, provider calls, tool routing and compression, using capture policy to decide what payload is recorded.

**Tech Stack:** Python 3.11 dataclasses and Protocols, pytest, stdlib `hashlib`/`json`/`base64`, optional OpenTelemetry packages behind the `observability` extra.

---

## Scope And References

Read before editing:

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/superpowers/specs/2026-05-04-production-observability-design.md`
- `../ai-knowledge/wiki/evaluation-observability.md`
- `../ai-knowledge/wiki/_patterns/otel-eval-bridge.md`
- `../ai-knowledge/wiki/hooks.md`

Scope:

- Implement capture policy, redaction, snapshots and provider usage.
- Implement an agentos-owned tracer protocol with no-op and in-memory test tracers.
- Implement instrumentation proxies and `instrument_query_loop(...)`.
- Implement OTel and Langfuse OTLP factory helpers behind optional dependency imports.
- Replace the small OpenAI agent's print-only trace path with optional observability instrumentation while keeping `--trace` for human debugging.

Out of scope:

- Streaming spans.
- W3C trace propagation to MCP servers or subagents.
- Langfuse prompt/dataset/score APIs.
- Pricing table based cost calculation.

## File Structure

Create:

- `src/agentos/observability/config.py`: `CapturePolicy`, `ObservabilityConfig`, default redactor and JSON/length helpers.
- `src/agentos/observability/conventions.py`: string constants for Langfuse, GenAI and agentos attributes.
- `src/agentos/observability/tracer.py`: `Tracer`, `Span`, `NoOpTracer`, `InMemoryTracer`, `InMemorySpanRecord`.
- `src/agentos/observability/snapshots.py`: provider/tool snapshots, stable hashing and policy-aware serialization.
- `src/agentos/observability/instrumented.py`: proxy classes for query loop, request builder, provider, router and compression.
- `src/agentos/observability/instrument.py`: public `instrument_query_loop(...)`.
- `tests/observability/test_capture_policy.py`
- `tests/observability/test_snapshots.py`
- `tests/observability/test_in_memory_tracer.py`
- `tests/observability/test_instrumented_provider.py`
- `tests/observability/test_instrumented_router.py`
- `tests/observability/test_query_loop_instrumentation.py`
- `tests/observability/test_otel_config.py`

Modify:

- `src/agentos/providers/base.py`: add `ProviderUsage` and extra `ProviderResponse` metadata fields.
- `src/agentos/providers/openai.py`: map OpenAI usage/model/response id when present.
- `src/agentos/providers/anthropic.py`: map Anthropic usage/model/id when present.
- `src/agentos/providers/openai_compatible.py`: map JSON usage/model/id fields.
- `src/agentos/providers/__init__.py`: export `ProviderUsage`.
- `src/agentos/observability/__init__.py`: export production observability API while keeping EventLog exports.
- `src/agentos/observability/langfuse.py`: add endpoint/header helpers; keep compatibility adapter.
- `src/agentos/observability/otel.py`: add optional factory helpers; keep compatibility adapter.
- `src/agentos/examples/small_openai_agent.py`: add `--observe-langfuse`/env-based instrumentation and keep `--trace`.
- `tests/providers/test_adapters.py`
- `tests/providers/test_openai_compatible.py`
- `tests/examples/test_small_openai_agent.py`
- `pyproject.toml`

## Task 1: Provider Usage Metadata

**Files:**

- Modify: `src/agentos/providers/base.py`
- Modify: `src/agentos/providers/__init__.py`
- Modify: `src/agentos/providers/openai.py`
- Modify: `src/agentos/providers/anthropic.py`
- Modify: `src/agentos/providers/openai_compatible.py`
- Test: `tests/providers/test_adapters.py`
- Test: `tests/providers/test_openai_compatible.py`

- [ ] **Step 1: Write failing provider usage tests**

Add assertions that OpenAI, Anthropic and OpenAI-compatible adapters populate `ProviderResponse.usage`, `model`, `provider_name`, and `response_id` when fake provider responses expose those fields.

Expected usage objects:

```python
ProviderUsage(
    input_tokens=10,
    output_tokens=5,
    total_tokens=15,
    cached_input_tokens=2,
    reasoning_output_tokens=1,
)
```

For Anthropic cache creation:

```python
ProviderUsage(
    input_tokens=10,
    output_tokens=5,
    cache_creation_input_tokens=3,
    cached_input_tokens=2,
)
```

- [ ] **Step 2: Run provider tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/providers/test_adapters.py tests/providers/test_openai_compatible.py -q
```

Expected: fails because `ProviderUsage` or response metadata fields do not exist.

- [ ] **Step 3: Implement usage model and adapter mapping**

Add `ProviderUsage` to `providers/base.py`, extend `ProviderResponse`, and map known response usage fields without breaking fake clients that omit usage.

- [ ] **Step 4: Run provider tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/providers/test_adapters.py tests/providers/test_openai_compatible.py -q
```

Expected: all selected provider tests pass.

## Task 2: Capture Policy, Redaction And Snapshots

**Files:**

- Create: `src/agentos/observability/config.py`
- Create: `src/agentos/observability/snapshots.py`
- Create: `src/agentos/observability/conventions.py`
- Modify: `src/agentos/observability/__init__.py`
- Test: `tests/observability/test_capture_policy.py`
- Test: `tests/observability/test_snapshots.py`

- [ ] **Step 1: Write failing capture policy tests**

Tests must prove:

- metadata mode records lengths and hashes only.
- redacted mode removes secret-like values.
- full local mode captures raw prompt/tool payload subject to max length.
- canonical hashes are stable for equivalent dict ordering.

- [ ] **Step 2: Run capture policy tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_capture_policy.py tests/observability/test_snapshots.py -q
```

Expected: import failures for new modules.

- [ ] **Step 3: Implement config and snapshot modules**

Implement:

- `CaptureMode = Literal["metadata", "redacted", "full"]`
- `Redactor = Callable[[object], object]`
- `CapturePolicy.metadata_only()`
- `CapturePolicy.redacted()`
- `CapturePolicy.full_for_local_development()`
- `ObservabilityConfig`
- deterministic snapshot helpers and SHA-256 canonical JSON hashing.

- [ ] **Step 4: Run capture policy tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_capture_policy.py tests/observability/test_snapshots.py -q
```

Expected: all selected tests pass.

## Task 3: Tracer Protocol And In-Memory Tracer

**Files:**

- Create: `src/agentos/observability/tracer.py`
- Modify: `src/agentos/observability/__init__.py`
- Test: `tests/observability/test_in_memory_tracer.py`

- [ ] **Step 1: Write failing tracer tests**

Tests must prove:

- nested spans preserve parent/child ids.
- attributes and events are recorded.
- context manager records exception status and re-raises.

- [ ] **Step 2: Run tracer tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_in_memory_tracer.py -q
```

Expected: import failure for `InMemoryTracer`.

- [ ] **Step 3: Implement tracer protocol, no-op tracer and in-memory tracer**

Use `contextvars` for current span id. `InMemorySpanRecord` should expose name, span_id, parent_span_id, attributes, events and status.

- [ ] **Step 4: Run tracer tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_in_memory_tracer.py -q
```

Expected: selected tests pass.

## Task 4: Provider And Tool Instrumentation

**Files:**

- Create: `src/agentos/observability/instrumented.py`
- Modify: `src/agentos/observability/__init__.py`
- Test: `tests/observability/test_instrumented_provider.py`
- Test: `tests/observability/test_instrumented_router.py`

- [ ] **Step 1: Write failing provider/router instrumentation tests**

Tests must prove:

- `InstrumentedProvider.complete()` creates a `provider.complete` generation span.
- usage, model, stop reason and tool call count are attributes.
- `InstrumentedToolCallRouter.execute_tool_call()` creates `tool.<name>` spans.
- denied tool calls set error status and re-raise the original error.

- [ ] **Step 2: Run instrumentation tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_instrumented_provider.py tests/observability/test_instrumented_router.py -q
```

Expected: imports fail for proxy classes.

- [ ] **Step 3: Implement provider and router proxies**

Implementation must delegate to the wrapped objects and never modify request, response, arguments or result objects.

- [ ] **Step 4: Run instrumentation tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_instrumented_provider.py tests/observability/test_instrumented_router.py -q
```

Expected: selected tests pass.

## Task 5: QueryLoop Instrumentation

**Files:**

- Create: `src/agentos/observability/instrument.py`
- Modify: `src/agentos/observability/instrumented.py`
- Modify: `src/agentos/observability/__init__.py`
- Test: `tests/observability/test_query_loop_instrumentation.py`

- [ ] **Step 1: Write failing query loop integration tests**

Use a fake provider that first returns one `read_file` tool call and then returns the final answer. Assert the span tree:

```text
agent.turn
├─ compression.maybe_compress
├─ provider.request.build
├─ provider.complete
├─ tool.read_file
├─ compression.maybe_compress
├─ provider.request.build
└─ provider.complete
```

Also assert final answer is unchanged and raw original loop components are not mutated.

- [ ] **Step 2: Run query loop instrumentation tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_query_loop_instrumentation.py -q
```

Expected: import failure or missing spans.

- [ ] **Step 3: Implement `InstrumentedQueryLoop`, request builder proxy, compression proxy and `instrument_query_loop(...)`**

Use a shallow copied `QueryLoop` configured with wrapped components. The root wrapper owns `agent.turn`.

- [ ] **Step 4: Run query loop instrumentation tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_query_loop_instrumentation.py -q
```

Expected: selected tests pass.

## Task 6: OTel And Langfuse Factory Helpers

**Files:**

- Modify: `src/agentos/observability/langfuse.py`
- Modify: `src/agentos/observability/otel.py`
- Modify: `src/agentos/observability/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/observability/test_otel_config.py`

- [ ] **Step 1: Write failing OTel config tests**

Tests must prove:

- Langfuse endpoint helper returns `{host}/api/public/otel/v1/traces`.
- Auth header is Basic `base64(public_key:secret_key)`.
- `x-langfuse-ingestion-version` equals `4`.
- importing `agentos.observability` succeeds without OTel installed.

- [ ] **Step 2: Run OTel config tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_otel_config.py -q
```

Expected: missing helpers or optional extra.

- [ ] **Step 3: Implement helpers and optional dependency extra**

Keep OpenTelemetry imports inside factory functions so base imports remain dependency-free.

- [ ] **Step 4: Run OTel config tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_otel_config.py -q
```

Expected: selected tests pass.

## Task 7: Small OpenAI Agent Observability Entry

**Files:**

- Modify: `src/agentos/examples/small_openai_agent.py`
- Modify: `tests/examples/test_small_openai_agent.py`
- Optional modify: `docs/examples/langfuse-otel-sequence.md`

- [ ] **Step 1: Write failing small agent tests**

Tests must prove:

- `build_agent(..., observability_config=config)` returns an instrumented loop when config is provided.
- `main(["--observe-langfuse", "hello"])` uses `create_langfuse_otel_tracer(...)` and `instrument_query_loop(...)`.
- Existing `--trace` print-debug behavior still works.

- [ ] **Step 2: Run small agent tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/examples/test_small_openai_agent.py -q
```

Expected: missing observability config parameter/flag.

- [ ] **Step 3: Implement small agent observability wiring**

Add env-based Langfuse setup:

- `LANGFUSE_HOST` or `LANGFUSE_BASE_URL`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `AGENTOS_OBSERVABILITY_CAPTURE=metadata|redacted|full`

Do not require Langfuse env vars unless `--observe-langfuse` is passed.

- [ ] **Step 4: Run small agent tests and verify green**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/examples/test_small_openai_agent.py -q
```

Expected: selected tests pass.

## Task 8: Full Verification And Commit

**Files:** all changed files.

- [ ] **Step 1: Run full tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run compileall**

Run:

```bash
uv run --python 3.11 --extra dev python -m compileall -q src tests scripts
```

Expected: exit 0.

- [ ] **Step 3: Run whitespace and drift checks**

Run:

```bash
git diff --check
rg -n "agent[O]s|agent[_]os" src tests docs pyproject.toml AGENTS.md .gitignore
rg -n "from opentelemetry|import opentelemetry|langfuse" src/agentos/runtime src/agentos/providers src/agentos/capabilities src/agentos/context
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance" tests/context/goldens src/agentos/context/renderer.py
```

Expected: first command exit 0; old-name search has no matches; forbidden-import search has no matches; default renderer metadata search has no forbidden prompt matches.

- [ ] **Step 4: Commit implementation**

Run:

```bash
git add src tests docs pyproject.toml
git commit -m "feat: add production observability instrumentation"
```

Expected: implementation commit on `feature/production-observability`.
