---
name: agent-os-agent-forms
description: What agent forms (archetypes) the SDK can build today — use to quickly map user requirements to modules and identify gaps. Updated 2026-05-16.
---

# Agent Forms Coverage

Use this reference when a user describes what they want to build. Match their intent to a form below, then route to the corresponding modules.

## Fully Supported (build directly)

### 1. Single-Turn Conversational Agent
**Modules**: `Agent.run()` + `Provider` + `ContextRenderer`
**Typical use**: customer support Q&A, content generation, translation, summarization
**Minimal code**:
```python
agent = AgentBuilder().provider(provider).build()
result = agent.run("Translate this to French: ...")
```

### 2. Tool-Calling Agent
**Modules**: `ToolRegistry` + `ToolCallRouter` + `SecurityPolicy` + `RegisteredTool`
**Typical use**: code execution, API orchestration, data queries, file manipulation
**Key config**: `AgentBuilder().tools([tool1, tool2]).build()`

### 3. MCP-Integrated Agent
**Modules**: `MCPRegistry` + `MCPClient` Protocol + `MCPToolAdapter`
**Typical use**: connecting to external MCP servers (filesystem, database, custom services)
**Key config**: Register `MCPServerRegistration` with a client implementing `MCPClient` Protocol

### 4. Streaming Agent
**Modules**: `Agent.stream()` / `stream_sse()` / `stream_jsonl()` / `async_stream()`
**Typical use**: real-time chat UI, SSE push, JSONL over HTTP
**Output formats**: typed `TurnStreamEvent`, SSE string, JSONL string

### 5. HTTP-Deployed Agent
**Modules**: `AsgiAgentApp` + `AgentSessionProvider` + `ChannelAuthPolicy`
**Typical use**: backend service deployment, multi-tenant SaaS, API gateway
**Key config**: ASGI app wraps session provider; deploy with uvicorn/gunicorn

### 6. Long-Conversation Agent
**Modules**: `CompressionRuntime` + `BudgetPolicy` + `RecallRuntime` + `CompressionIndex`
**Typical use**: multi-turn deep dialogue, research assistants, long-running workflows
**Key mechanism**: automatic compression when context budget exceeded; recall by handle or query

### 7. Multi-Agent Local Coordination
**Modules**: `AgentCoordinator` + `TaskTable` + `AgentInbox` + `SpawnExecutor`
**Typical use**: expert dispatch, parallel subtask execution, specialist routing
**Modes**: `spawn` (ephemeral subagent) or `dispatch` (send to persistent expert)

### 8. Multi-Agent Distributed
**Modules**: `PostgresTaskStore` + `RedisAgentMessageQueue` + `A2AAdapter` + `RemoteTaskExecutor`
**Typical use**: cross-service agent collaboration, microservice agent mesh
**Key mechanism**: CAS task state in Postgres, delivery via Redis Streams, A2A HTTP for remote

### 9. Async Agent
**Modules**: `Agent.async_run()` / `async_stream()` + `AsyncProvider` Protocol
**Typical use**: embedding in FastAPI / aiohttp / Starlette without blocking event loop
**Key mechanism**: `AsyncQueryLoop` 是 native async，原生 await provider 和 tool I/O；`Agent.async_stream` 自动选择 native async（如果 query_loop 提供 async `run_turn_stream`）或 sync-in-executor fallback。注册 async tool handler 时必须用 `AsyncQueryLoop`。

### 10. Observable Agent
**Modules**: `ObservabilityConfig` + `OTel tracer` + `Langfuse` + `EventLog` + `Snapshots`
**Typical use**: production monitoring, debugging, cost tracking, compliance audit
**Key config**: `instrument_query_loop()` wraps each boundary with instrumented adapter

### 11. Session-Persistent Agent
**Modules**: `SessionSnapshot` + `SessionPersistence` (SQLite / Postgres / Filesystem)
**Typical use**: conversation resume, checkpoint recovery, multi-device continuity
**Key mechanism**: serialize full session state (messages + context + compression index) to durable store

### 12. Interruptible Agent
**Modules**: `Agent.interrupt()` + `QueryLoop._interrupted` checkpoint
**Typical use**: timeout control, user cancel, cost budget enforcement
**Key mechanism**: interrupt flag checked before each provider call and tool execution

### 13. Hook-Guarded Agent
**Modules**: `HookManager` + `HookHandler` Protocol + 4 hook points
**Typical use**: approval workflows, content filtering, audit logging, request/response modification
**Hook points**: `before_provider_call`, `after_provider_call`, `before_tool_call`, `after_tool_call`
**Actions**: `allow` / `deny` / `modify`

### 14. Callback Agent
**Modules**: `Agent.run_with_callbacks()` + typed callback signatures
**Typical use**: UI integration, progress reporting, custom event handling
**Callbacks**: `on_event`, `on_content_delta`, `on_thinking_delta`, `on_tool_started`, `on_tool_completed`

### 15. Continuation Agent
**Modules**: `ContinuationTrigger` + `Agent.run_continuation()` + `TurnNoticeProvider`
**Typical use**: agent wakes when subtask completes, event-driven multi-turn without user input
**Key mechanism**: notice provider feeds runtime notices; continuation stream runs without user message

---

## Partially Supported (needs glue code)

### 16. RAG Agent
**Available**: `RecallRuntime` + `MemoryRuntime` + `QdrantRecallIndex` + query recall
**Missing**: document ingestion pipeline (chunking + embedding + indexing)
**Workaround**: implement `RecallIndex.index_segment()` with your own chunker, or feed documents as `CompressedSegmentPackage`

### 17. Scheduled / Cron Agent
**Available**: `AsgiAgentApp` + HTTP channel
**Missing**: no built-in scheduler
**Workaround**: external cron / task queue calls `/turn` endpoint on schedule

### 18. Human-in-the-Loop Agent
**Available**: `HookManager.before_tool_call` → deny/modify; callback events
**Missing**: standardized approval UI protocol
**Workaround**: hook denies sensitive tools, returns "awaiting approval" message; external UI calls back to approve

### 19. Agent with Dynamic Discovery
**Available**: `PersistentAgentRegistry` + `PostgresAgentRegistryStore` + `AgentResolver`
**Missing**: automated heartbeat → offline marking; health check integration
**Workaround**: external health checker updates registry status

---

## Not Supported (requires significant extension)

| Form | Gap |
|------|-----|
| Graph / DAG Workflow Agent | No state graph engine; only linear tool loop |
| Code Interpreter Agent | No sandboxed execution environment |
| Voice / Multimodal Streaming Agent | Provider supports ImagePart/FilePart but no audio streaming |
| Self-Evolving Agent | No meta-learning or self-prompt-modification |
| Browser Automation Agent | No built-in browser driver integration |

---

## Decision Tree

```
User wants to build an agent:
├── Single conversation, no tools?        → Form 1 (Conversational)
├── Needs to call APIs / execute code?    → Form 2 (Tool-Calling)
├── Connects to MCP servers?              → Form 3 (MCP-Integrated)
├── Real-time streaming UI?               → Form 4 (Streaming)
├── Deploy as HTTP service?               → Form 5 (HTTP-Deployed)
├── Long conversations (>20 turns)?       → Form 6 (Long-Conversation)
├── Multiple specialist agents?
│   ├── Same process?                     → Form 7 (Multi-Agent Local)
│   └── Cross-service?                    → Form 8 (Multi-Agent Distributed)
├── Running in async framework?           → Form 9 (Async)
├── Needs monitoring / tracing?           → Form 10 (Observable)
├── Must survive restarts?                → Form 11 (Session-Persistent)
├── Needs cancel / timeout?               → Form 12 (Interruptible)
├── Approval / filtering required?        → Form 13 (Hook-Guarded)
├── Custom UI progress callbacks?         → Form 14 (Callback)
├── Event-driven wake-on-result?          → Form 15 (Continuation)
├── Search over past conversations?       → Form 16 (RAG, partial)
├── Runs on schedule?                     → Form 17 (Scheduled, partial)
└── Human approval for sensitive ops?     → Form 18 (HITL, partial)
```

## Combining Forms

Most production agents combine multiple forms. Common combos:

| Combo | Forms |
|-------|-------|
| Production API agent | 2 + 4 + 5 + 10 + 11 + 12 |
| Research assistant | 2 + 6 + 10 + 16 |
| Multi-agent service | 7 or 8 + 5 + 10 + 13 |
| Chat with tools + memory | 2 + 4 + 6 + 11 |
| Enterprise compliance agent | 2 + 5 + 10 + 13 + 18 |
