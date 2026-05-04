# Phase 3-4 Small Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 3 runtime state/hooks and Phase 4 provider tool-call loop for a small deterministic agent.

**Architecture:** Runtime state and hooks stay in `runtime/` and `hooks/`; tools stay in `capabilities/`; security policy stays in `policies/`; providers only normalize provider input/output. `QueryLoop` remains the orchestrator and does not execute concrete tools directly.

**Tech Stack:** Python 3.11 dataclasses, Protocol types, pytest, uv.

---

## File Structure

- Create `src/agentos/runtime/session.py`: session lifecycle state.
- Create `src/agentos/runtime/turn.py`: turn lifecycle state.
- Create `src/agentos/runtime/event_bus.py`: typed runtime events and in-memory event bus.
- Create `src/agentos/hooks/base.py`: hook protocol and failure policy.
- Create `src/agentos/hooks/registry.py`: ordered hook registration.
- Create `src/agentos/hooks/manager.py`: hook dispatch runtime.
- Create `src/agentos/capabilities/tools.py`: tool declarations and registered tools.
- Create `src/agentos/capabilities/registry.py`: `ToolRegistry`.
- Create `src/agentos/capabilities/executor.py`: `ToolExecutor`.
- Create `src/agentos/capabilities/router.py`: `ToolCallRouter`.
- Create `src/agentos/capabilities/builtin.py`: safe `read_file` tool for local tests.
- Create `src/agentos/policies/security.py`: security allow/deny checks.
- Modify `src/agentos/providers/base.py`: provider tool call response model.
- Modify `src/agentos/providers/fake.py`: fake provider can return tool calls.
- Create `src/agentos/providers/openai.py`: thin import-free OpenAI adapter.
- Create `src/agentos/providers/anthropic.py`: thin import-free Anthropic adapter.
- Modify `src/agentos/runtime/query_loop.py`: run provider tool-call loop.
- Add tests under `tests/runtime/`, `tests/hooks/`, `tests/capabilities/`, and `tests/providers/`.

### Task 1: Runtime State And Hooks

- [x] **Step 1: Write failing tests for session, turn, event bus, and hooks**

Run: `uv run --python 3.11 --extra dev pytest tests/runtime/test_state_events.py tests/hooks/test_runtime.py -q`

Expected: imports fail because Phase 3 modules do not exist.

- [x] **Step 2: Implement minimal runtime state and hooks**

Implement dataclasses and ordered hook dispatch with `continue` and `raise` failure policies.

- [x] **Step 3: Verify Phase 3 tests pass**

Run: `uv run --python 3.11 --extra dev pytest tests/runtime/test_state_events.py tests/hooks/test_runtime.py -q`

Expected: all Phase 3 tests pass.

### Task 2: Tool System And Security

- [x] **Step 1: Write failing tool registry/executor/security tests**

Run: `uv run --python 3.11 --extra dev pytest tests/capabilities/test_tools.py -q`

Expected: imports fail because Phase 4 capability modules do not exist.

- [x] **Step 2: Implement tools, registry, executor, capability runtime, and security policy**

Implement provider schemas, handler execution, context/external routing, and deny-before-handler behavior.

- [x] **Step 3: Verify capability tests pass**

Run: `uv run --python 3.11 --extra dev pytest tests/capabilities/test_tools.py -q`

Expected: capability tests pass.

### Task 3: Provider Tool-Call Loop

- [x] **Step 1: Write failing small agent loop test**

Run: `uv run --python 3.11 --extra dev pytest tests/runtime/test_tool_loop.py -q`

Expected: failure because `QueryLoop` does not execute provider tool calls yet.

- [x] **Step 2: Implement provider response tool calls and loop orchestration**

Extend `ProviderResponse`, `FakeProvider`, and `QueryLoop` so tool calls append assistant messages, execute through `ToolCallRouter`, append tool results, and repeat until final answer.

- [x] **Step 3: Verify small agent loop passes**

Run: `uv run --python 3.11 --extra dev pytest tests/runtime/test_tool_loop.py -q`

Expected: fake small agent reads `pyproject.toml` and answers `agent-os`.

### Task 4: Provider Adapters And Full Verification

- [x] **Step 1: Write provider adapter tests with fake clients**

Run: `uv run --python 3.11 --extra dev pytest tests/providers/test_adapters.py -q`

Expected: imports fail before adapters exist.

- [x] **Step 2: Implement import-free OpenAI and Anthropic adapters**

Adapters accept injected clients and normalize text/tool-call responses into `ProviderResponse`.

- [x] **Step 3: Run all verification**

Run:

```bash
uv run --python 3.11 --extra dev pytest -q
uv run --python 3.11 --extra dev python -m compileall -q src tests
git diff --check
git diff --cached --check
```

Expected: all tests pass, compile succeeds, and diff checks are clean.

## Self-Review

- Spec coverage: Phase 3 state/hooks and Phase 4 provider/tool loop all have tasks.
- Placeholder scan: no deferred implementation placeholders.
- Type consistency: public names use `SessionState`, `TurnState`, `EventBus`, `HookManager`, `ToolRegistry`, `ToolExecutor`, `ToolCallRouter`, and `SecurityPolicy`.
