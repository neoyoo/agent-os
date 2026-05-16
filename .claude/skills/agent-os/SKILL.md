---
name: agent-os-sdk
description: >
  Use when building agents with the agent-os SDK — guides requirements gathering,
  architecture decisions, spec generation, and parallel implementation handoff.
  Triggers on: "build an agent", "create agent", "new agent project", "use agent-os".
---

# agent-os SDK — Agent Development Skill

## Overview

Guides a developer from idea → running agent using the agent-os SDK. The process is phased: first understand what they're building, then generate a spec, then hand off to subagents for parallel implementation.

**agent-os is a harness SDK** — it provides the runtime (QueryLoop, ContextRuntime, MessageRuntime, Providers, Compression, Hooks, Channels, Multi-agent) and the developer configures + extends it. No subclassing, no framework lock-in.

## When to Use

- Developer says they want to build an agent
- Developer asks how to use agent-os for a specific use case
- Developer has requirements and wants architecture guidance
- Developer wants to understand which agent-os modules apply to their problem

## Process

```
flow/01-requirements.md   → Phased questions to understand the agent
flow/02-spec-generation.md → Produce agent-spec.yaml from answers
flow/03-implementation.md  → Hand off to subagents via superpowers workflow
```

## Routing

| User intent | Load |
|-------------|------|
| "I want to build an agent" / "new agent" | `flow/01-requirements.md` |
| "Generate the spec" / already answered questions | `flow/02-spec-generation.md` |
| "Start implementation" / spec exists | `flow/03-implementation.md` |
| "What kind of agent can I build?" / capability check | `modules/agent-forms.md` |
| Specific module question | `modules/<module>.md` |

## SDK Source Location

The agent-os SDK lives at the project root. Key paths:

```
src/agentos/
├── runtime/          → Agent, QueryLoop, AsyncQueryLoop, ProviderRequestBuilder
├── providers/        → Provider protocol, Anthropic/OpenAI adapters, typed messages
├── context/          → ContextRuntime, ContextRenderer, WorkingState, Chapters
├── messages/         → MessageRuntime, Message, MessageRef
├── capabilities/     → ToolCallRouter, ToolRegistry, RegisteredTool
├── compression/      → CompressionRuntime, RuleBasedCompressor, LlmCompressor
├── hooks/            → HookManager, HookRegistry, lifecycle hook points
├── channels/         → AsgiAgentApp, HttpAgentChannel, A2A adapter
├── multi/            → AgentCoordinator, TaskStore, AgentMessageQueue, local/remote dispatch
├── memory/           → MemoryRuntime, HotSessionStore, DurableSessionStore, Redis/Postgres
├── persistence/      → SessionSnapshot, SessionPersistence, SQLite/Postgres/FileSystem
├── observability/    → TraceContext, W3C propagation, EventRecord
├── context_protocol.py → 5 built-in context tools (declare_schema, update_state, etc.)
└── builder.py        → AgentBuilder (recommended entry point)
```

## Module Dependency Graph

```
AgentBuilder
    │
    ├── Provider (Anthropic / OpenAI / custom)
    ├── ToolCallRouter
    │       ├── Context Protocol Tools (built-in, always wired)
    │       ├── External Tools (RegisteredTool + handler)
    │       └── MCP Tools (optional)
    ├── ContextRuntime → ContextRenderer → system prompt
    ├── MessageRuntime → active message window
    ├── CompressionRuntime (optional) → long sessions
    ├── HookManager (optional) → lifecycle interception
    └── EventBus (optional) → typed event observation

Channels (ASGI / A2A) ← feed requests to Agent
Persistence (Redis + Postgres) ← externalize session state for multi-node
Multi-agent (TaskStore + AgentMessageQueue + Coordinator) ← orchestrate sub-agents
```

## Multi-Agent Runtime Boundaries

Use `AgentCoordinator` for orchestration. Inject protocol boundaries when a deployment needs distributed task state or message delivery:

| Boundary | In-memory adapter | Production adapter |
|----------|-------------------|--------------------|
| `TaskStore` | `TaskTable` | `PostgresTaskStore` |
| `AgentMessageQueue` | `AgentInbox` | `RedisAgentMessageQueue` |

`TaskStore` is the truth source for task/result state. `AgentMessageQueue` is delivery/notification only.

Source: `src/agentos/multi/task_store.py`, `src/agentos/multi/message_queue.py`, `src/agentos/multi/tasks.py`, `src/agentos/multi/inbox.py`, `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`, `tests/multi/test_coordinator_distributed_boundaries.py`.
