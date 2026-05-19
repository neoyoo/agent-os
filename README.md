# agent-os

Production-grade agent runtime SDK. Provides the harness — you configure and extend it.

## What This Is

agent-os is a **runtime SDK for building AI agents**. It handles the hard parts (query loop, context management, compression, tool routing, multi-agent coordination, persistence, channels) so you focus on your agent's unique capabilities.

It is **not** a chatbot wrapper. It is **not** a framework you subclass. It's a composable set of runtime modules with Protocol-based boundaries.

## Quickstart

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .build()
)

result = agent.run("What is Python?")
print(result.content)
```

## Install

```bash
# Core
pip install -e .

# With Redis hot store
pip install -e ".[redis]"

# With Postgres durable store
pip install -e ".[postgres]"
```

## Architecture

```
AgentBuilder
    │
    ├── Provider (Anthropic / OpenAI / custom)
    ├── ToolCallRouter
    │       ├── Context Protocol Tools (built-in: declare_schema, update_state, etc.)
    │       ├── External Tools (your RegisteredTool handlers)
    │       └── MCP Tools (optional)
    ├── ContextRuntime → working state, chapters, inherited state
    ├── MessageRuntime → active message window
    ├── CompressionRuntime → long session context management
    ├── HookManager → lifecycle interception
    └── EventBus → typed observation events

Channels (ASGI / A2A) ← HTTP access
Persistence (Redis + Postgres) ← multi-node session state
Multi-agent (TaskStore + AgentMessageQueue + Coordinator / A2A) ← agent orchestration
```

## Modules

| Module | Purpose | Key Types |
|--------|---------|-----------|
| `runtime/` | Agent facade + QueryLoop | `Agent`, `QueryLoop`, `AsyncQueryLoop` |
| `providers/` | LLM adapters | `AnthropicProvider`, `OpenAIProvider`, `ProviderRequest`, `ProviderResponse` |
| `context/` | Cognitive state management | `ContextRuntime`, `ContextRenderer`, `WorkingStateSchema` |
| `messages/` | Message store + windowing | `MessageRuntime`, `Message`, `MessageRef` |
| `capabilities/` | Tool routing + execution | `ToolCallRouter`, `ToolRegistry`, `RegisteredTool` |
| `compression/` | Long-context compression | `CompressionRuntime`, `RuleBasedCompressor`, `LlmCompressor` |
| `hooks/` | Lifecycle hooks | `HookManager`, `HookRegistry` |
| `channels/` | HTTP/SSE/A2A serving | `AsgiAgentApp`, `A2AServer` |
| `multi/` | Multi-agent coordination | `AgentCoordinator`, `TaskStore`, `AgentMessageQueue`, A2A dispatch |
| `memory/` | Hot + durable state stores | `RedisHotSessionStore`, `MemoryRuntime` |
| `persistence/` | Session snapshots | `SessionSnapshot`, `SQLitePersistence`, `PostgresDurableSessionStore` |
| `observability/` | Tracing + events | `TraceContext`, W3C propagation, `EventRecord` |

## Usage Guide

The full usage guide lives in `.claude/skills/agent-os/`:

```
.claude/skills/agent-os/
├── SKILL.md                     ← Entry point + module map
├── flow/
│   ├── 01-requirements.md       ← Requirements gathering (6 dimensions)
│   ├── 02-spec-generation.md    ← Spec blueprint schema
│   └── 03-implementation.md     ← Project scaffold + parallel dev
└── modules/
    ├── quick-start.md           ← Copy-paste code patterns (8 scenarios)
    ├── architecture.md          ← Data flow + boundaries + extension points
    ├── persistence.md           ← Redis/Postgres multi-node state
    ├── multi-agent.md           ← Local spawn + A2A distributed
    ├── testing.md               ← FakeProvider + testing patterns
    └── anti-patterns.md         ← 10 common mistakes to avoid
```

This guide doubles as a **Claude Code / Codex skill** — when loaded into an AI coding assistant, it provides interactive guidance for building agents with this SDK.

## Scenarios

| Scenario | What you need |
|----------|---------------|
| CLI tool / script | `AgentBuilder` + `agent.run()` |
| HTTP API | `AgentBuilder` + `AsgiAgentApp` |
| Streaming UI | `agent.stream()` or `agent.async_stream()` |
| Long sessions | `.with_compression()` |
| Multi-node deploy | `RedisHotSessionStore` + `PostgresDurableSessionStore` |
| Sub-agent orchestration | `AgentCoordinator` + `TaskStore`/`AgentMessageQueue`; A2A for endpoint-backed agents |
| Custom tools | `RegisteredTool(name, description, parameters, handler)` |
| Lifecycle hooks | `HookRegistry.register("before_tool_call", handler)` |

## Context Protocol

Every agent built with agent-os has access to 6 built-in context tools that the model uses to manage its own cognitive state and image re-inspection:

| Tool | Purpose |
|------|---------|
| `declare_schema` | Declare working state fields for the current chapter |
| `update_state` | Update a working state field value |
| `extend_schema` | Add fields when current schema is insufficient |
| `start_chapter` | Start new chapter when task changes substantially |
| `recall_context` | Retrieve compressed text/history by handle or semantic query; returned content is a normal tool result |
| `load_image` | Load an uploaded image attachment into the next provider request for re-inspection |

These are automatically wired by `AgentBuilder` — you don't need to register them.

`recall_context` is only for compressed or semantic text recall. Attachment handles use `load_image(handle="att:...")`; the current attachment runtime projects image content only.

## Tests

```bash
uv run pytest -q
```

## License

Private.
