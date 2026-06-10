# Context Budget Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship production hardening for context budget pressure without changing the LLM-visible context protocol.

**Architecture:** Implement in three independently verifiable phases. Phase 1 adds shared token counting and tool-result caps at the tool result ingestion boundary. Phase 2 makes compression token-aware and resilient to compressor failures. Phase 3 adds SSE resume by separating turn event production from SSE readers.

**Tech Stack:** Python 3.11 dataclasses/protocols, pytest, existing `QueryLoop` / `AsyncQueryLoop`, `EventBus`, `CompressionRuntime`, and ASGI channel code.

---

## Scope Contract

This plan covers the three specs added on 2026-05-28:

- `docs/superpowers/specs/2026-05-28-tool-result-token-budget-design.md`
- `docs/superpowers/specs/2026-05-28-token-aware-compression-circuit-breaker-design.md`
- `docs/superpowers/specs/2026-05-28-sse-resume-mid-turn-design.md`

This branch completed the context-budget hardening path in five focused commits:

- `feat: cap oversized tool results`
- `feat: add token-aware compression budget`
- `feat: add sse stream resume`
- `feat: add redis sse event buffer`
- `feat: add execution backend seam`

Implemented in this branch:

- Implement `TokenCounter` and `HeuristicTokenCounter`.
- Implement `ToolResultBudget`.
- Cap tool results in both `QueryLoop` and `AsyncQueryLoop`.
- Emit typed `ToolResultCappedEvent`.
- Make compression token-aware and resilient to compressor failures.
- Add SSE mid-turn resume with in-memory and Redis event buffers.
- Add the execution backend / resource policy seam for later sandbox hardening.

Deferred to later branches:

- Phase A async runtime completion.
- Phase B persistent session provider.
- Phase C cross-machine A2A dispatch transport hardening.
- Phase E production backpressure, concurrency limits, observability, and load baseline.
- `RunEventStore`, `RuntimeProfile`, workspace/permission binding, AgentCard interoperability, and symbolic context map / evidence recall.

Design rules in force:

- No new default context-protocol tool.
- Overflowing tool content must not enter message history.
- Query loop must stay orchestration-focused; token counting and nudge construction live outside the loop.
- Typed runtime events only; no loose event string.

---

## File Structure

Phase 1 files:

- Create `src/agentos/tokens/__init__.py`: public token exports.
- Create `src/agentos/tokens/counter.py`: `TokenCounter`, `HeuristicTokenCounter`.
- Create `src/agentos/policies/tool_result_budget.py`: `ToolResultBudget`, `cap_tool_result_content`.
- Modify `src/agentos/policies/__init__.py`: export `ToolResultBudget`.
- Modify `src/agentos/events/types.py`: add `ToolResultCappedEvent`.
- Modify `src/agentos/events/__init__.py`: export `ToolResultCappedEvent`.
- Modify `src/agentos/runtime/event_bus.py`: compatibility export.
- Modify `src/agentos/runtime/query_loop.py`: apply cap before `append_tool_result`.
- Modify `src/agentos/runtime/async_query_loop.py`: apply same cap on async path.
- Modify `src/agentos/builder.py`: wire default budget/counter into built loops.
- Test `tests/tokens/test_counter.py`.
- Test `tests/policies/test_tool_result_budget.py`.
- Test `tests/runtime/test_tool_result_budget.py`.

Phase 2 files:

- Modify `src/agentos/policies/budget.py`: add `CompressionBudget` protocol and `TokenBudgetPolicy`.
- Modify `src/agentos/compression/evictor.py`: accept the protocol.
- Modify `src/agentos/compression/runtime.py`: failure retention + fallback compressor.
- Add `CompressionFailedEvent` exports.
- Extend compression tests.

Phase 3 files:

- Create `src/agentos/channels/sse_buffer.py`.
- Create `src/agentos/channels/sse_turns.py`.
- Modify `src/agentos/channels/asgi.py`.
- Extend ASGI channel tests.

---

## Phase 1: Tool-Result Token Budget

### Task 1: Token Counter

**Files:**
- Create: `src/agentos/tokens/__init__.py`
- Create: `src/agentos/tokens/counter.py`
- Test: `tests/tokens/test_counter.py`

- [x] **Step 1: Write failing token counter tests**

Create `tests/tokens/test_counter.py`:

```python
from dataclasses import dataclass

from agentos.messages import Message
from agentos.tokens import HeuristicTokenCounter


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str


def test_heuristic_counter_counts_text_with_ceiling() -> None:
    counter = HeuristicTokenCounter(char_per_token=4)

    assert counter.count_text("abcde") == 2


def test_heuristic_counter_counts_messages_and_tools() -> None:
    counter = HeuristicTokenCounter(char_per_token=4)
    message = Message(id="msg_1", role="tool", content="abcdefgh")

    total = counter.count_messages(
        [message],
        tools=[ToolSpec(name="read_file", description="Read a file")],
    )

    assert total > counter.count_text(message.content)
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/tokens/test_counter.py -q`

Expected: import failure because `agentos.tokens` does not exist.

- [x] **Step 3: Implement minimal counter**

Implement `TokenCounter` and `HeuristicTokenCounter` with JSON-safe serialization for dicts, dataclasses, and objects exposing `to_provider_dict()`.

- [x] **Step 4: Run tests and verify GREEN**

Run: `uv run pytest tests/tokens/test_counter.py -q`

Expected: both tests pass.

### Task 2: Tool Result Budget

**Files:**
- Create: `src/agentos/policies/tool_result_budget.py`
- Modify: `src/agentos/policies/__init__.py`
- Test: `tests/policies/test_tool_result_budget.py`

- [x] **Step 1: Write failing budget tests**

Create `tests/policies/test_tool_result_budget.py`:

```python
from agentos.policies import ToolResultBudget
from agentos.policies.tool_result_budget import cap_tool_result_content
from agentos.tokens import HeuristicTokenCounter


def test_tool_result_budget_uses_override_before_default() -> None:
    budget = ToolResultBudget(default_max_tokens=100, overrides={"read_file": 3})

    assert budget.cap_for("read_file") == 3
    assert budget.cap_for("grep") == 100


def test_tool_result_budget_env_overrides_all(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_TOOL_RESULT_MAX_TOKENS", "7")
    budget = ToolResultBudget(default_max_tokens=100, overrides={"read_file": 3})

    assert budget.cap_for("read_file") == 7


def test_cap_tool_result_replaces_oversized_content_with_small_nudge() -> None:
    capped = cap_tool_result_content(
        tool_name="read_file",
        content="x" * 100,
        budget=ToolResultBudget(default_max_tokens=5),
        token_counter=HeuristicTokenCounter(char_per_token=1),
    )

    assert capped.capped is True
    assert capped.actual_tokens == 100
    assert capped.cap == 5
    assert "read_file" in capped.content
    assert "100" in capped.content
    assert "xxxxx" not in capped.content
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/policies/test_tool_result_budget.py -q`

Expected: import failure because `ToolResultBudget` does not exist.

- [x] **Step 3: Implement budget and nudge helper**

Implement `ToolResultBudget.cap_for()` and `cap_tool_result_content(...)`. Invalid env values should be ignored so a bad deployment variable does not crash every tool result append.

- [x] **Step 4: Run tests and verify GREEN**

Run: `uv run pytest tests/policies/test_tool_result_budget.py -q`

Expected: all tests pass.

### Task 3: Query Loop Integration

**Files:**
- Modify: `src/agentos/events/types.py`
- Modify: `src/agentos/events/__init__.py`
- Modify: `src/agentos/runtime/event_bus.py`
- Modify: `src/agentos/runtime/query_loop.py`
- Modify: `src/agentos/runtime/async_query_loop.py`
- Modify: `src/agentos/builder.py`
- Test: `tests/runtime/test_tool_result_budget.py`

- [x] **Step 1: Write failing integration tests**

Create `tests/runtime/test_tool_result_budget.py` with one sync and one async test. Both should execute a tool returning large content and assert the message history contains the nudge, not the original content.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/runtime/test_tool_result_budget.py -q`

Expected: constructor/import failure or oversized content still present.

- [x] **Step 3: Add typed event and loop cap helper**

Add `ToolResultCappedEvent`. Add loop fields `tool_result_budget` and `token_counter`. Add a small helper on `QueryLoop` to cap a `ToolExecutionResult`; `AsyncQueryLoop` should call the sync loop helper to avoid duplicated policy logic.

- [x] **Step 4: Wire builder defaults**

`AgentBuilder` should pass `ToolResultBudget()` and `HeuristicTokenCounter()` unless the user supplied a custom loop manually.

- [x] **Step 5: Run target tests**

Run: `uv run pytest tests/tokens/test_counter.py tests/policies/test_tool_result_budget.py tests/runtime/test_tool_result_budget.py -q`

Expected: all target tests pass.

### Task 4: Phase 1 Verification

- [x] Run `uv run ruff check src/ tests/`
- [x] Run `uv run pytest -q`
- [x] Run `uv run python -m compileall -q src tests`
- [x] Run `git diff --check`
- [x] Commit Phase 1:

```bash
git add docs/superpowers/specs/2026-05-28-*.md docs/superpowers/plans/2026-05-28-context-budget-hardening.md src tests
git commit -m "feat: cap oversized tool results"
```

---

## Phase 2: Token-Aware Compression

Start only after Phase 1 is merged or green on this branch.

Acceptance:

- [x] `TokenBudgetPolicy` triggers based on effective token window.
- [x] Tools schema overhead can be included through `static_overhead_tokens`.
- [x] Compressor exceptions do not remove active refs.
- [x] Consecutive failures emit `CompressionFailedEvent`.
- [x] Fallback compressor path succeeds and resets failure count.

---

## Phase 3: SSE Mid-Turn Resume

Start only after a separate review checkpoint.

Acceptance:

- [x] SSE event IDs use `<turn_stream_id>:<sequence>`.
- [x] POST streaming clients can replay with explicit `Last-Event-ID` header.
- [x] Disconnect enters grace instead of immediate interrupt.
- [x] Terminal retention does not collide with the next turn in the same session.
- [x] Existing heartbeat behavior remains id-free.

---

## Phase D: Execution Backend Seam

Implemented after the original three-phase budget plan to prepare sandbox and
resource-policy work without changing tool behavior.

Acceptance:

- [x] `ExecutionBackend` protocol exists.
- [x] `InProcessExecutionBackend` preserves current sync and async handler behavior.
- [x] `ResourcePolicy` carries deadline, memory, and network allowlist declarations.
- [x] `ToolExecutor` and `ToolCallRouter` accept the seam without changing public behavior.

---

## Deferred Roadmap Items

These specs were added for the broader 2026-05-28 production hardening roadmap,
but are outside this branch's implemented scope:

- Phase A: `docs/superpowers/specs/2026-05-28-async-runtime-completion-design.md`
- Phase B: `docs/superpowers/specs/2026-05-28-persistent-session-provider-design.md`
- Phase C: `docs/superpowers/specs/2026-05-28-cross-machine-a2a-dispatch-design.md`
- Phase E: `docs/superpowers/specs/2026-05-28-production-hardening-backpressure-design.md`

Next iteration inputs are captured in
`docs/plans/2026-06-10-kb-refresh-agentos-sdk-iteration.md`.

---

## Branch Completion Checklist

| Requirement | Implementation file(s) | Test file(s) or verification command | Status |
|---|---|---|---|
| Tool-result token cap | `src/agentos/policies/tool_result_budget.py`, `src/agentos/runtime/query_loop.py`, `src/agentos/runtime/async_query_loop.py` | `tests/policies/test_tool_result_budget.py`, `tests/runtime/test_tool_result_budget.py` | complete |
| Shared token counter | `src/agentos/tokens/counter.py` | `tests/tokens/test_counter.py` | complete |
| Token-aware compression and circuit breaker | `src/agentos/policies/budget.py`, `src/agentos/compression/evictor.py`, `src/agentos/compression/runtime.py` | `tests/policies/test_token_budget_policy.py`, `tests/compression/test_runtime.py` | complete |
| SSE mid-turn resume | `src/agentos/channels/asgi.py`, `src/agentos/channels/sse_turns.py` | `tests/channels/test_asgi_app.py`, `tests/channels/test_asgi_app_async.py` | complete |
| In-memory and Redis SSE event buffers | `src/agentos/channels/sse_buffer.py` | `tests/channels/test_sse_buffer.py` | complete |
| Execution backend seam | `src/agentos/capabilities/backend.py`, `src/agentos/policies/resource_policy.py`, `src/agentos/capabilities/executor.py`, `src/agentos/capabilities/router.py` | `tests/capabilities/test_execution_backend.py` | complete |
| Async runtime completion | later Phase A spec | not implemented in this branch | deferred |
| Persistent session provider | later Phase B spec | not implemented in this branch | deferred |
| Cross-machine A2A transport hardening | later Phase C spec | not implemented in this branch | deferred |
| Production backpressure and load baseline | later Phase E spec | not implemented in this branch | deferred |

---

## Self-Review

- Spec coverage: Current branch scope is complete. The broader 2026-05-28 hardening roadmap is partially complete because Phase A/B/C/E remain deferred.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: `TokenCounter` uses `object` messages/tools because it must estimate both internal `Message` and provider-facing structures.
