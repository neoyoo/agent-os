# Phase 7 Memory Compression Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Phase 7 slice: compression-aware memory recall with package compression, storage ABCs, local fakes, and query-based `recall_context`.

**Architecture:** Compression produces a `CompressedSegmentPackage` containing the LLM-visible segment, source refs, and a recall document. `MemoryRuntime` records the package through hot, durable, and recall-index boundaries, while `RecallRuntime` supports both exact handle recall and query recall. Production middleware adapters are optional boundaries; core runtime remains free of Redis, Qdrant, Postgres, OTel, and Langfuse imports.

**Tech Stack:** Python 3.11 dataclasses/protocols, pytest, existing `agentos` context/messages/compression/recall modules, optional extras for Redis/Qdrant/Postgres client packages.

---

## File Structure

- Create `src/agentos/memory/types.py`: `CompressedSegmentPackage`, `SegmentRecallDocument`, `RecallCandidate`, and `HotSessionState`.
- Create `src/agentos/memory/store.py`: `HotSessionStore` and `DurableSessionStore` protocols.
- Create `src/agentos/memory/recall_index.py`: `RecallIndex` protocol.
- Create `src/agentos/memory/embeddings.py`: `TextEmbeddingProvider` protocol.
- Create `src/agentos/memory/in_memory.py`: test/local implementations of hot store, durable store, and lexical recall index.
- Create `src/agentos/memory/runtime.py`: `MemoryRuntime`.
- Create `src/agentos/memory/redis_store.py`, `src/agentos/memory/qdrant_index.py`, `src/agentos/persistence/postgres.py`: optional adapter stubs with clear dependency errors.
- Modify `src/agentos/memory/__init__.py`: public exports.
- Modify `src/agentos/compression/compressor.py`: add `PackageCompressor` and `RuleBasedCompressor.compress_package()`.
- Modify `src/agentos/compression/runtime.py`: add optional `CompressionMemorySink` and record packages before removing refs.
- Modify `src/agentos/recall/runtime.py`: add optional `MemoryRuntime`, `session_id`, and query recall.
- Modify `src/agentos/context_protocol.py`: expand `recall_context` schema and description.
- Modify `src/agentos/__init__.py`: export key Phase 7 memory names.
- Modify `pyproject.toml`: add optional dependencies for `redis`, `qdrant`, `postgres`, and `production-memory`.

## Scope Contract

This plan implements the approved spec `docs/superpowers/specs/2026-05-05-phase-7-memory-compression-middleware-design.md`.

This plan completes:

- Package compression and segment recall documents.
- Memory storage ABCs and local fake implementations.
- Compression-to-memory sink integration.
- `recall_context(handle=...)` compatibility and `recall_context(query=...)`.
- Optional middleware adapter module boundaries and extras.
- Prompt metadata boundary tests.

This plan defers:

- Real network integration tests against Redis/Qdrant/Postgres services.
- Background reindex workers.
- Multimodal/image recall.
- Subagent, AgentCard, and AgentRegistry.
- HTTP channel session hydration.

## Task 1: Memory Types And Protocols

**Files:**
- Create: `src/agentos/memory/types.py`
- Create: `src/agentos/memory/store.py`
- Create: `src/agentos/memory/recall_index.py`
- Create: `src/agentos/memory/embeddings.py`
- Modify: `src/agentos/memory/__init__.py`
- Test: `tests/memory/test_types.py`

- [ ] **Step 1: Write failing public type tests**

Create `tests/memory/test_types.py` with assertions that import the new memory types, build a `CompressedSegmentPackage`, and verify `SegmentRecallDocument.to_text()` includes topic/summary/keywords/tool hints but not original message text.

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/memory/test_types.py -q`

Expected: import failure for `agentos.memory`.

- [ ] **Step 3: Implement memory dataclasses and protocols**

Add frozen dataclasses for `SegmentRecallDocument`, `CompressedSegmentPackage`, `RecallCandidate`, and `HotSessionState`. Add `Protocol` classes for `HotSessionStore`, `DurableSessionStore`, `RecallIndex`, and `TextEmbeddingProvider`.

- [ ] **Step 4: Run the test and verify it passes**

Run: `pytest tests/memory/test_types.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add src/agentos/memory tests/memory/test_types.py && git commit -m "feat: add memory recall types"`

## Task 2: Local Memory Stores And Recall Index

**Files:**
- Create: `src/agentos/memory/in_memory.py`
- Test: `tests/memory/test_in_memory.py`

- [ ] **Step 1: Write failing tests for local stores**

Create tests showing `InMemoryHotSessionStore` stores hot messages and segment refs, `InMemoryDurableSessionStore` stores messages and compressed segment packages, and `InMemoryRecallIndex` returns a candidate for a query matching a recall document.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/memory/test_in_memory.py -q`

Expected: import failure for `agentos.memory.in_memory`.

- [ ] **Step 3: Implement local fake stores**

Implement in-memory classes using copied lists/tuples to avoid exposing mutable internals. The lexical recall index should score candidates by simple case-insensitive token overlap over `SegmentRecallDocument.to_text()`.

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/memory/test_in_memory.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add src/agentos/memory/in_memory.py tests/memory/test_in_memory.py && git commit -m "feat: add local memory stores"`

## Task 3: RuleBased Package Compression

**Files:**
- Modify: `src/agentos/compression/compressor.py`
- Test: `tests/compression/test_package_compressor.py`

- [ ] **Step 1: Write failing package compressor tests**

Create tests showing `RuleBasedCompressor.compress_package("seg_1", "session_1", messages)` returns a package with visible segment, source refs, recall document keywords including file paths/tool names, and searchable text that does not copy entire long original content.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/compression/test_package_compressor.py -q`

Expected: `RuleBasedCompressor` has no `compress_package`.

- [ ] **Step 3: Implement `PackageCompressor` and `compress_package()`**

Keep existing `compress()` behavior. Add `compress_package()` that calls `compress()`, builds `source_refs` from message ids, extracts deterministic keywords/tool hints, and clips searchable text.

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/compression/test_package_compressor.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add src/agentos/compression/compressor.py tests/compression/test_package_compressor.py && git commit -m "feat: package compressed segments for recall"`

## Task 4: MemoryRuntime

**Files:**
- Create: `src/agentos/memory/runtime.py`
- Modify: `src/agentos/memory/__init__.py`
- Test: `tests/memory/test_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Create tests showing `MemoryRuntime.record_compressed_segment()` writes hot refs, durable package, and recall index; `recall_by_handle()` prefers hot messages and falls back to durable messages; `recall_by_query()` searches the recall index and returns original messages.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/memory/test_runtime.py -q`

Expected: import failure for `MemoryRuntime`.

- [ ] **Step 3: Implement `MemoryRuntime`**

Use the store protocols and keep behavior synchronous. Deduplicate message ids across multiple candidates while preserving order.

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/memory/test_runtime.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add src/agentos/memory/runtime.py src/agentos/memory/__init__.py tests/memory/test_runtime.py && git commit -m "feat: coordinate memory recall runtime"`

## Task 5: CompressionRuntime Memory Sink Integration

**Files:**
- Modify: `src/agentos/compression/runtime.py`
- Test: `tests/compression/test_memory_sink.py`

- [ ] **Step 1: Write failing integration tests**

Create tests showing `CompressionRuntime(memory_sink=...)` records a package before removing active refs, keeps active refs if memory sink fails, and preserves old behavior when no sink is configured.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/compression/test_memory_sink.py -q`

Expected: `CompressionRuntime.__init__()` rejects `memory_sink`.

- [ ] **Step 3: Implement `CompressionMemorySink` integration**

Add a protocol and optional constructor field. When configured, call `compress_package()` if present, otherwise adapt old `compress()` result into a package. Record the package after appending the visible segment and before removing active refs.

- [ ] **Step 4: Run targeted compression tests**

Run: `pytest tests/compression/test_memory_sink.py tests/compression/test_runtime.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add src/agentos/compression/runtime.py tests/compression/test_memory_sink.py && git commit -m "feat: record compression packages in memory sink"`

## Task 6: Query RecallRuntime And Tool Schema

**Files:**
- Modify: `src/agentos/recall/runtime.py`
- Modify: `src/agentos/context_protocol.py`
- Test: `tests/recall/test_query_recall.py`
- Test: `tests/capabilities/test_tools.py`

- [ ] **Step 1: Write failing query recall tests**

Create tests showing `RecallRuntime(memory_runtime=..., session_id="session_1").recall_context(query="pyproject", limit=1)` injects the matched messages once, rejects calls with both handle and query, and raises a clear error if query recall is requested without memory runtime.

- [ ] **Step 2: Add failing tool schema assertions**

Extend existing capability/router tests to call `recall_context` with `query` through `ToolCallRouter`.

- [ ] **Step 3: Run tests and verify they fail**

Run: `pytest tests/recall/test_query_recall.py tests/capabilities/test_tools.py -q`

Expected: `recall_context()` does not accept query yet or tool schema rejects query.

- [ ] **Step 4: Implement query recall and schema**

Keep `handle` compatibility. Add a `recall_context(handle=None, query=None, limit=1)` Python API and update router argument handling. Expand provider schema with `handle`, `query`, and `limit`, and validate exactly one of handle/query.

- [ ] **Step 5: Run targeted recall/router tests**

Run: `pytest tests/recall tests/capabilities/test_tools.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run: `git add src/agentos/recall/runtime.py src/agentos/context_protocol.py tests/recall/test_query_recall.py tests/capabilities/test_tools.py && git commit -m "feat: support query based context recall"`

## Task 7: Optional Middleware Adapter Boundaries

**Files:**
- Create: `src/agentos/memory/redis_store.py`
- Create: `src/agentos/memory/qdrant_index.py`
- Create: `src/agentos/persistence/postgres.py`
- Modify: `pyproject.toml`
- Test: `tests/memory/test_optional_adapters.py`

- [ ] **Step 1: Write failing adapter boundary tests**

Create tests showing adapter modules import without optional dependencies, but constructing `RedisHotSessionStore`, `QdrantRecallIndex`, or `PostgresDurableSessionStore` raises a helpful `RuntimeError` if the respective client package is unavailable.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/memory/test_optional_adapters.py -q`

Expected: adapter modules do not exist.

- [ ] **Step 3: Implement adapter stubs and extras**

Implement constructor-level optional dependency checks. Add extras `redis`, `qdrant`, `postgres`, and `production-memory` in `pyproject.toml`.

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/memory/test_optional_adapters.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add src/agentos/memory/redis_store.py src/agentos/memory/qdrant_index.py src/agentos/persistence/postgres.py pyproject.toml tests/memory/test_optional_adapters.py && git commit -m "feat: add production memory adapter boundaries"`

## Task 8: Public API And Architecture Guards

**Files:**
- Modify: `src/agentos/__init__.py`
- Test: `tests/architecture/test_public_api.py`
- Test: `tests/architecture/test_phase7_memory_boundaries.py`
- Test: `tests/context/test_renderer.py`

- [ ] **Step 1: Write failing public API and boundary tests**

Add tests that `agentos` and `agentos.memory` export the Phase 7 names, `runtime/` does not import Redis/Qdrant/Postgres modules, `context/renderer.py` does not import `agentos.memory`, and default renderer output does not contain storage metadata terms.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/architecture/test_public_api.py tests/architecture/test_phase7_memory_boundaries.py tests/context/test_renderer.py -q`

Expected: public exports are missing.

- [ ] **Step 3: Implement exports and any renderer test adjustments**

Export only stable ABC/types/runtime names. Do not add storage metadata to the default renderer.

- [ ] **Step 4: Run targeted architecture tests**

Run: `pytest tests/architecture tests/context/test_renderer.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add src/agentos/__init__.py tests/architecture/test_public_api.py tests/architecture/test_phase7_memory_boundaries.py tests/context/test_renderer.py && git commit -m "feat: expose phase 7 memory api"`

## Task 9: Full Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run full test suite**

Run: `pytest`

Expected: all tests pass.

- [ ] **Step 2: Run compile check**

Run: `python -m compileall -q src tests`

Expected: exit code 0.

- [ ] **Step 3: Run diff whitespace check**

Run: `git diff --check`

Expected: exit code 0.

- [ ] **Step 4: Run architecture drift searches**

Run:

```bash
rg -n "redis|qdrant|psycopg|postgres" src/agentos/runtime src/agentos/context src/agentos/messages src/agentos/providers src/agentos/capabilities
rg -n "message_id|session_id|source_refs|qdrant|redis|postgres|score" tests/context/goldens src/agentos/context/renderer.py
```

Expected: first command has no matches except none; second command has no matches in default renderer/goldens.

- [ ] **Step 5: Final commit if verification required file changes**

If verification required changes, commit them with a focused message. If no changes, skip commit.
