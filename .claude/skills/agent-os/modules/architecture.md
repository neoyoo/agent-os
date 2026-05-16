---
name: agent-os-architecture
description: How agent-os SDK modules connect — data flow, boundaries, extension points. Read when you need to understand WHY the SDK is structured this way.
---

# Architecture Reference

## Core Loop Data Flow

```
User message
    │
    ▼
QueryLoop.run_turn_stream(message)
    │
    ├── 1. MessageRuntime.append(user_message)
    │
    ├── 2. HookManager.dispatch("before_provider_call")
    │
    ├── 3. ProviderRequestBuilder.build_request()
    │       ├── ContextRenderer.render(context_state)  → system prompt
    │       ├── MessageRuntime.active_window()         → messages
    │       └── tools (from ToolCallRouter.tool_specs()) → tool schemas
    │
    ├── 4. Provider.complete(request) → ProviderResponse
    │
    ├── 5. HookManager.dispatch("after_provider_call")
    │
    ├── 6. If response has tool_calls:
    │       ├── HookManager.dispatch("before_tool_call")
    │       ├── ToolCallRouter.execute_tool_call(tc)
    │       │       ├── Context protocol tool? → ContextRuntime mutation
    │       │       ├── MCP tool? → MCPToolAdapter.execute()
    │       │       └── External tool? → ToolExecutor → RegisteredTool.handler()
    │       ├── HookManager.dispatch("after_tool_call")
    │       ├── MessageRuntime.append(tool_result)
    │       └── GOTO step 2 (tool loop, max_tool_iterations=8)
    │
    ├── 7. MessageRuntime.append(assistant_message)
    │
    ├── 8. CompressionRuntime.maybe_compress() (if budget exceeded)
    │
    └── 9. Yield TurnStreamCompleted(content)
```

## Module Boundaries (Protocol-based)

Each module exposes a Protocol. QueryLoop only knows the Protocol, not the implementation.

| Module | Protocol | QueryLoop sees as |
|--------|----------|-------------------|
| Context | `ContextRuntimeBoundary` | `.snapshot()`, `.set_runtime_notices()`, `.clear_runtime_notices()` |
| Tools | `ToolCallRouterBoundary` | `.execute_tool_call(tool_call)` |
| Turn notices | `TurnNoticeProvider` | `.consume_notices()` |
| Provider | `Provider` | `.complete(request)` |

This means you can swap any module without touching QueryLoop.

## ContextRuntime

Manages the agent's structured cognitive state:

```
ContextState
├── working_state_schema: WorkingStateSchema (field declarations)
├── working_state: dict[str, str | list[str]]  (current values)
├── inherited_state: dict[str, str | list[str]] (from previous chapter)
├── compressed_history: list[CompressedSegment]  (summaries)
├── memory_context: str  (injected cross-session memory)
└── runtime_notices: tuple[str, ...]  (one-shot system messages)
```

The model mutates this via context protocol tools:
- `declare_schema(fields)` — declare working state fields for this chapter
- `update_state(field_name, value)` — update a field
- `extend_schema(fields)` — add fields when schema insufficient
- `start_chapter(fields?)` — start new chapter (current state → inherited)
- `recall_context(handle?, query?, limit?)` — retrieve compressed segments

## ContextRenderer

Turns ContextState into the system prompt string. Sections:

1. **Runtime Contract** — identity + security guardrails
2. **Capability Plane** — available tools summary (human-readable)
3. **Context Management Rules** — instructions for using context tools
4. **Declared Schema** — current chapter's field definitions
5. **Working State** — current field values
6. **Inherited State** — from previous chapter
7. **Compressed History** — segment summaries with handles
8. **Memory Context** — cross-session memory
9. **Runtime Notices** — one-shot messages (consumed after rendering)

## CompressionRuntime

Triggers when `len(active_messages) > budget.max_active_messages`:
1. Selects oldest messages (keeps `retain_latest_messages` most recent)
2. Feeds selected messages to `Compressor.compress()`
3. Produces `CompressedSegment(id, topic, summary)`
4. Removes compressed messages from active window
5. Adds segment to `compressed_history` in ContextState
6. If MemoryRuntime attached, indexes for recall

## Extension Points

| Want to... | Use... |
|------------|--------|
| Add a tool | `RegisteredTool` + `AgentBuilder.tools()` |
| Intercept before/after | `HookManager` + register at hook points |
| Observe events | `EventBus` + typed event subscriptions |
| Custom system prompt | `AgentBuilder.context_renderer(custom)` |
| Custom compression | Implement `Compressor` protocol, pass to `.with_compression(compressor)` |
| Custom provider | Implement `Provider` protocol (just `.complete()`) |
| Custom state storage | Implement `HotSessionStore` / `DurableSessionStore` protocols |
| Custom channel | Wrap `Agent` in your own HTTP/WebSocket/gRPC handler |

## What AgentBuilder Does

AgentBuilder is syntactic sugar. It:
1. Creates ContextRuntime (with EventBus if provided)
2. Creates MessageRuntime
3. Creates ToolRegistry + ToolCallRouter (with context protocol tools)
4. Creates ContextRenderer (with capability plane from tools)
5. Creates ProviderRequestBuilder (renderer + messages + tool schemas)
6. Creates CompressionRuntime if requested
7. Assembles all into `Agent(query_loop_kwargs={...})`

You can skip AgentBuilder entirely and construct Agent directly if you need more control.
