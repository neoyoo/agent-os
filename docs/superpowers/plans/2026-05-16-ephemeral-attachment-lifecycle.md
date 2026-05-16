# Ephemeral Attachment Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session-scoped attachment upload, one-shot provider expansion, placeholder folding, and `recall_context(handle="att:...")` re-expansion for OpenAI and Anthropic providers.

**Architecture:** Attachments live in a new `agentos.attachments` subsystem. `MessageRuntime` remains the message truth source and stores placeholders, while `AttachmentRuntime` owns bytes/metadata and one-shot expansion state. Provider adapters map canonical content parts to OpenAI/Anthropic payloads and reject unsupported media deterministically.

**Tech Stack:** Python dataclasses, existing `ProviderMessage` typed union, existing `ProviderRequestBuilder`, `ToolCallRouter`, and pytest fake clients.

---

### Task 1: Attachment Types And Runtime

**Files:**
- Create: `src/agentos/attachments/types.py`
- Create: `src/agentos/attachments/store.py`
- Create: `src/agentos/attachments/runtime.py`
- Create: `src/agentos/attachments/__init__.py`
- Test: `tests/attachments/test_runtime.py`

- [ ] Write failing tests for `upload_bytes()`, placeholder privacy, one-shot expansion, and unknown attachment handle.
- [ ] Run `uv run pytest tests/attachments/test_runtime.py -q` and verify failures are from missing module/API.
- [ ] Implement dataclasses, in-memory store, and `AttachmentRuntime`.
- [ ] Run `uv run pytest tests/attachments/test_runtime.py -q` and verify pass.

### Task 2: Provider Content Parts

**Files:**
- Modify: `src/agentos/providers/messages.py`
- Modify: `src/agentos/providers/__init__.py`
- Test: `tests/providers/test_provider_messages.py`

- [ ] Write failing tests for `UserMessage(content=(TextPart(...), ImagePart(...)))` serialization and placeholder privacy.
- [ ] Run targeted provider message tests and verify failure.
- [ ] Add canonical content part types and preserve string content compatibility.
- [ ] Run targeted provider message tests and verify pass.

### Task 3: Request Builder Projection

**Files:**
- Modify: `src/agentos/runtime/provider_request_builder.py`
- Modify: `src/agentos/runtime/query_loop.py`
- Test: `tests/runtime/test_provider_request_builder.py`
- Test: `tests/runtime/test_query_loop.py`

- [ ] Write failing tests proving first request expands attachment and next request uses placeholder.
- [ ] Run targeted runtime tests and verify failure.
- [ ] Inject optional `AttachmentRuntime` into `ProviderRequestBuilder` and `QueryLoop.run_turn_stream(..., attachments=...)`.
- [ ] Run targeted runtime tests and verify pass.

### Task 4: `recall_context(att:...)` Routing

**Files:**
- Modify: `src/agentos/capabilities/router.py`
- Test: `tests/capabilities/test_tools.py`

- [ ] Write failing test proving `recall_context(handle="att:att_1")` schedules one-shot attachment expansion and does not call compression recall.
- [ ] Run targeted tool router test and verify failure.
- [ ] Add optional `AttachmentRuntime` to `ToolCallRouter` and route `att:` handles before existing recall runtime.
- [ ] Run targeted tool router test and verify pass.

### Task 5: OpenAI And Anthropic Mapping

**Files:**
- Modify: `src/agentos/providers/openai.py`
- Modify: `src/agentos/providers/anthropic.py`
- Test: `tests/providers/test_adapters.py`

- [ ] Write failing tests for OpenAI image content parts, Anthropic image content blocks, Anthropic PDF document blocks, and unsupported generic files.
- [ ] Run targeted provider adapter tests and verify failure.
- [ ] Implement adapter mapping and deterministic unsupported errors.
- [ ] Run targeted provider adapter tests and verify pass.

### Task 6: Agent API Surface

**Files:**
- Modify: `src/agentos/runtime/agent.py`
- Modify: `src/agentos/builder.py`
- Test: `tests/runtime/test_agent_stream_api.py`
- Test: `tests/examples/test_small_openai_agent.py`

- [ ] Write failing tests for `agent.attachments.upload_bytes(...)` and `agent.run(..., attachments=[...])`.
- [ ] Run targeted tests and verify failure.
- [ ] Expose `Agent.attachments` and thread attachments through sync/stream/async turn APIs.
- [ ] Run targeted tests and verify pass.

### Task 7: Verification

**Files:**
- All changed files.

- [ ] Run attachment/provider/runtime targeted test suite.
- [ ] Run full test suite: `uv run pytest -q`.
- [ ] Run compile check: `uv run python -m compileall -q src tests`.
- [ ] Run whitespace check: `git diff --check`.
- [ ] Run drift search for forbidden new tool name: `rg "view_attachment" src tests docs/superpowers/specs/2026-05-16-ephemeral-attachment-lifecycle-design.md -S`.
