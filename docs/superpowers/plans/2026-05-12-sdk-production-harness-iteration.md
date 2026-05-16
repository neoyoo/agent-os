# SDK Production Harness Iteration Plan

## Scope Contract

This task tracks the six 2026-05-12 production-harness specs after the Phase 8
channels review fixes are complete.

It belongs to the post-Phase-8 SDK usability and production-readiness track.
The goal is to improve the user-facing construction path, provider type safety,
long-context quality, extension hooks, async serving, and distributed tracing
without weakening the existing context-first module boundaries.

## Inputs

- `docs/superpowers/specs/2026-05-12-agent-builder-design.md`
- `docs/superpowers/specs/2026-05-12-strong-typed-provider-message-design.md`
- `docs/superpowers/specs/2026-05-12-llm-compressor-design.md`
- `docs/superpowers/specs/2026-05-12-hook-system-enhancement-design.md`
- `docs/superpowers/specs/2026-05-12-async-query-loop-design.md`
- `docs/superpowers/specs/2026-05-12-distributed-tracing-design.md`

## Implementation Order

1. `AgentBuilder v1`
   - Complete a small builder surface: provider, tools, compression, and direct
     component overrides.
   - Defer `system_prompt()`, memory, observability, async, and broad
     convenience presets until the underlying wiring is already present.
   - Defer `hook_manager()` / `with_hooks()` until HookManager has tested
     runtime call sites.
   - Use `ContextRenderer` / `RuntimeContract` as the future prompt extension
     path; do not add raw system prompt string override in v1.
   - Preserve the standard `Agent` facade and avoid a wrapper layer.

2. `Strong-typed ProviderMessage`
   - Replace provider-facing message dicts with frozen dataclasses.
   - Strong-type the existing canonical OpenAI-style function tool schema rather
     than changing provider tool semantics.
   - Do not flatten `ProviderToolSpec` into Anthropic-style
     `{name, description, input_schema}`.
   - Keep provider-specific dict conversion inside provider adapters.

3. `LlmCompressor`
   - Add LLM-based compression as an injectable compressor.
   - Keep `RuleBasedCompressor` as deterministic default and fallback.
   - Do not add implicit extra provider calls to the simplest agent path.

4. `Distributed Trace Context`
   - Reuse the existing W3C trace context propagator.
   - Add public helper APIs before wiring A2A server extraction.
   - Keep trace context out of default LLM prompts and message stores.

5. `Hook Wiring And Enhancement`
   - First wire the existing four hook points end-to-end into runtime paths.
   - Add new hook points only after each has a tested call site.
   - Add priority and decorator registration in the synchronous hook manager.
   - Defer async hooks until an async runtime path exists; do not bridge with
     `run_until_complete()` inside synchronous dispatch.

6. `Async QueryLoop`
   - Treat as a separate runtime phase, not a small channel fix.
   - Keep synchronous `QueryLoop` stable.
   - Use ASGI blocking behavior as a driver for this phase, but do not bury a
     partial async model inside channel adapters.
   - Do not change the existing synchronous `AgentSessionProvider` protocol in
     v1; add a parallel async provider later if needed.

## Review Fixes Required Before Starting

- Remote endpoint dispatch must require an explicitly injected
  `remote_task_executor`; no lazy fallback construction in `AgentCoordinator`.
- Test imports should work with `pythonpath = ["src"]`; avoid relying on project
  root insertion through `pythonpath = ["."]`.
- ASGI SSE synchronous iteration remains an accepted Phase 8 MVP limitation and
  is deferred to the async/offload phase above.
- The six specs must stay aligned with the corrections above before
  implementation starts.

## Verification Baseline

Each implementation step must run:

- Targeted tests for the changed module.
- Full `uv run pytest -q`.
- `uv run python -m compileall -q src tests`.
- `git diff --check`.
- Boundary drift checks relevant to the touched module.
