# Phase 2 Compression + Recall Design

## Goal

Phase 2 adds the context-management bridge that turns old active messages into LLM-visible compressed history and lets the model recover the original messages with `recall_context(handle="seg_...")`.

## Design References

- `docs/design/llm-context-only-example.md`
- `docs/design/sdk-architecture.md`
- `AGENTS.md`

## Scope

In scope:

- `BudgetPolicy` decides when the active window should compress.
- `Evictor` selects a contiguous oldest prefix without cutting `tool_use` / `tool_result` pairs.
- `RuleBasedCompressor` produces deterministic local summaries for tests and fallback.
- `CompressionIndex` maps `seg_...` handles to original message ids.
- `CompressionRuntime` appends `CompressedSegment` to `ContextState`, removes selected refs from `ActiveWindow`, and keeps originals in `MessageStore`.
- `RecallRuntime.recall_context(handle=...)` restores original messages as temporary active refs for exactly the next provider request.

Out of scope:

- LLM-based compression.
- Real token counting.
- Provider-specific tool-call sequence normalization.
- Persistence of compression indexes.
- Observability events for compression and recall.

## Runtime Flow

```text
MessageRuntime active window grows
  ↓
BudgetPolicy detects active message overflow
  ↓
Evictor selects oldest compressible refs
  ↓
RuleBasedCompressor reads original messages from MessageStore
  ↓
CompressionRuntime creates seg_1 and records CompressionIndex
  ↓
ContextRuntime appends CompressedSegment to ContextState
  ↓
ActiveWindow removes selected refs
```

Recall:

```text
recall_context(handle="seg_1")
  ↓
RecallRuntime resolves source message ids from CompressionIndex
  ↓
MessageRuntime injects temporary recalled refs before active refs
  ↓
next ProviderRequestBuilder build includes recalled original messages
  ↓
temporary refs are removed after that provider message materialization
```

## Acceptance

- Compression removes old refs from `ActiveWindow` but never deletes originals from `MessageStore`.
- The rendered prompt contains the compressed segment handle, for example `seg_1`.
- `CompressionIndex` keeps source message ids internal; default prompt does not render `source`, `message_id`, or `compression_id`.
- Eviction does not split an active assistant tool call from its tool result.
- `recall_context("seg_1")` injects restored original messages into exactly one provider request.
- `QueryLoop` runs compression before provider requests and preserves recalled temporary messages for the next request.
- Tests use deterministic fakes and do not perform network calls.
