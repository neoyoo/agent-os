# Phase 2 Compression + Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 2's deterministic compression and recall mainline.

**Architecture:** Compression owns summarization and segment indexes; messages own active refs and temporary recall injection; context owns only the LLM-visible compressed segment projection. The runtime bridge coordinates these modules without deleting original messages.

**Tech Stack:** Python 3.11 dataclasses, pytest, uv.

---

## File Structure

- Create `src/agentos/policies/budget.py`: active-window overflow policy.
- Create `src/agentos/compression/evictor.py`: safe oldest-prefix selection.
- Create `src/agentos/compression/compressor.py`: deterministic `RuleBasedCompressor`.
- Create `src/agentos/compression/index.py`: internal segment-to-message-id mapping.
- Create `src/agentos/compression/runtime.py`: compression orchestration.
- Create `src/agentos/recall/runtime.py`: `recall_context` orchestration.
- Modify `src/agentos/messages/types.py`: mark active refs as temporary or persistent.
- Modify `src/agentos/messages/window.py`: support temporary recalled refs and one-shot cleanup.
- Modify `src/agentos/messages/runtime.py`: expose temporary recall injection and consume temporary refs when provider messages are materialized.
- Modify `src/agentos/context/runtime.py`: add an explicit compressed-segment append API.
- Add `tests/compression/test_runtime.py` and `tests/recall/test_runtime.py`.

### Task 1: Compression Runtime

**Files:**
- Create: `src/agentos/policies/__init__.py`
- Create: `src/agentos/policies/budget.py`
- Create: `src/agentos/compression/__init__.py`
- Create: `src/agentos/compression/evictor.py`
- Create: `src/agentos/compression/compressor.py`
- Create: `src/agentos/compression/index.py`
- Create: `src/agentos/compression/runtime.py`
- Modify: `src/agentos/context/runtime.py`
- Test: `tests/compression/test_runtime.py`

- [x] **Step 1: Write failing compression tests**

```python
def test_compression_runtime_moves_old_refs_to_compressed_history() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Old requirement")
    old_assistant = message_runtime.append_assistant("Old analysis")
    current_user = message_runtime.append_user("Current task")
    current_assistant = message_runtime.append_assistant("Current answer")

    runtime = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=3, retain_latest_messages=2),
    )

    segment = runtime.maybe_compress()

    assert segment is not None
    assert segment.id == "seg_1"
    assert [message.id for message in message_runtime.materialize_active()] == [
        current_user.id,
        current_assistant.id,
    ]
    assert message_runtime.store.get(old_user.id).content == "Old requirement"
    assert message_runtime.store.get(old_assistant.id).content == "Old analysis"
    assert context_runtime.state.compressed_history == [segment]
    assert runtime.index.source_refs("seg_1") == [old_user.id, old_assistant.id]
```

- [x] **Step 2: Run test and verify RED**

Run: `uv run --python 3.11 --extra dev pytest tests/compression/test_runtime.py -q`

Expected: import failure because Phase 2 modules do not exist.

- [x] **Step 3: Implement compression modules**

Create budget policy, evictor, deterministic compressor, index, and runtime. Add `ContextRuntime.append_compressed_segment(segment)`.

- [x] **Step 4: Run compression tests and verify GREEN**

Run: `uv run --python 3.11 --extra dev pytest tests/compression/test_runtime.py -q`

Expected: compression tests pass.

### Task 2: Recall Runtime

**Files:**
- Modify: `src/agentos/messages/types.py`
- Modify: `src/agentos/messages/window.py`
- Modify: `src/agentos/messages/runtime.py`
- Create: `src/agentos/recall/__init__.py`
- Create: `src/agentos/recall/runtime.py`
- Test: `tests/recall/test_runtime.py`

- [x] **Step 1: Write failing recall tests**

```python
def test_recall_context_injects_original_messages_for_one_request() -> None:
    context_runtime = ContextRuntime()
    message_runtime = MessageRuntime()
    old_user = message_runtime.append_user("Original detail")
    message_runtime.append_assistant("Original answer")
    current_user = message_runtime.append_user("Current question")

    compression = CompressionRuntime(
        context_runtime=context_runtime,
        message_runtime=message_runtime,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    compression.maybe_compress()

    RecallRuntime(
        compression_index=compression.index,
        message_runtime=message_runtime,
    ).recall_context("seg_1")

    first_request = message_runtime.materialize_provider_messages()
    second_request = message_runtime.materialize_provider_messages()

    assert [message["content"] for message in first_request] == [
        "Original detail",
        "Original answer",
        "Current question",
    ]
    assert [message["content"] for message in second_request] == ["Current question"]
    assert message_runtime.store.get(old_user.id).content == "Original detail"
```

- [x] **Step 2: Run test and verify RED**

Run: `uv run --python 3.11 --extra dev pytest tests/recall/test_runtime.py -q`

Expected: import failure or missing temporary-ref behavior.

- [x] **Step 3: Implement recall and temporary refs**

Add `temporary` to `MessageRef`, temporary prepend/cleanup in `ActiveWindow`, one-shot provider materialization in `MessageRuntime`, and `RecallRuntime.recall_context`.

- [x] **Step 4: Run recall tests and verify GREEN**

Run: `uv run --python 3.11 --extra dev pytest tests/recall/test_runtime.py -q`

Expected: recall tests pass.

### Task 3: Full Verification

**Files:**
- All Phase 2 files.
- Test: `tests/runtime/test_query_loop.py`

- [x] **Step 0: Run actual loop verification**

Run: `uv run --python 3.11 --extra dev pytest tests/runtime/test_query_loop.py::test_query_loop_runs_compression_and_recall_through_provider_requests -q`

Expected: PASS. This proves `QueryLoop` compresses before the second provider request, renders `seg_1`, and keeps recalled temporary messages for exactly one request.

- [x] **Step 1: Run full test suite**

Run: `uv run --python 3.11 --extra dev pytest -q`

Expected: all tests pass.

- [x] **Step 2: Run compile check**

Run: `uv run --python 3.11 --extra dev python -m compileall -q src tests`

Expected: exit code 0.

- [x] **Step 3: Stage changes**

Run: `git add AGENTS.md docs/design/sdk-architecture.md docs/superpowers/specs/2026-05-03-phase-2-compression-recall-design.md docs/superpowers/plans/2026-05-03-phase2-compression-recall.md src tests`

Expected: Phase 2 docs and implementation are staged.

## Self-Review

- Spec coverage: every acceptance item in the Phase 2 spec maps to a test or implementation step.
- Placeholder scan: no `TBD`, `TODO`, or deferred behavior in this plan.
- Type consistency: public names use `BudgetPolicy`, `CompressionRuntime`, `CompressionIndex`, and `RecallRuntime.recall_context`.
