# agent-os

Production-grade agent runtime SDK. Provides the harness 鈥?you configure and extend it.

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
    鈹?
    鈹溾攢鈹€ Provider (Anthropic / OpenAI / custom)
    鈹溾攢鈹€ ToolCallRouter
    鈹?      鈹溾攢鈹€ Context Protocol Tools (built-in: declare_schema, update_state, etc.)
    鈹?      鈹溾攢鈹€ External Tools (your RegisteredTool handlers)
    鈹?      鈹斺攢鈹€ MCP Tools (optional)
    鈹溾攢鈹€ ContextRuntime 鈫?working state, chapters, inherited state
    鈹溾攢鈹€ MessageRuntime 鈫?active message window
    鈹溾攢鈹€ CompressionRuntime 鈫?long session context management
    鈹溾攢鈹€ HookManager 鈫?lifecycle interception
    鈹斺攢鈹€ EventBus 鈫?typed observation events

Channels (ASGI / A2A) 鈫?HTTP access
Persistence (Redis + Postgres) 鈫?multi-node session state
Multi-agent (TaskStore + AgentMessageQueue + Coordinator / A2A) 鈫?agent orchestration
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
鈹溾攢鈹€ SKILL.md                     鈫?Entry point + module map
鈹溾攢鈹€ flow/
鈹?  鈹溾攢鈹€ 01-requirements.md       鈫?Requirements gathering (6 dimensions)
鈹?  鈹溾攢鈹€ 02-spec-generation.md    鈫?Spec blueprint schema
鈹?  鈹斺攢鈹€ 03-implementation.md     鈫?Project scaffold + parallel dev
鈹斺攢鈹€ modules/
    鈹溾攢鈹€ quick-start.md           鈫?Copy-paste code patterns (8 scenarios)
    鈹溾攢鈹€ architecture.md          鈫?Data flow + boundaries + extension points
    鈹溾攢鈹€ persistence.md           鈫?Redis/Postgres multi-node state
    鈹溾攢鈹€ multi-agent.md           鈫?Local spawn + A2A distributed
    鈹溾攢鈹€ testing.md               鈫?FakeProvider + testing patterns
    鈹斺攢鈹€ anti-patterns.md         鈫?10 common mistakes to avoid
```

This guide doubles as a **Claude Code / Codex skill** 鈥?when loaded into an AI coding assistant, it provides interactive guidance for building agents with this SDK.

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

Every agent built with agent-os has access to 7 built-in context tools that the model uses to manage its own cognitive state:

| Tool | Purpose |
|------|---------|
| `declare_schema` | Declare working state fields for the current chapter |
| `update_state` | Update a working state field value |
| `extend_schema` | Add fields when current schema is insufficient |
| `start_chapter` | Start new chapter when task changes substantially |
| `recall_context` | Retrieve compressed history segments by handle or query |
| `load_attachment` | Load an uploaded attachment into the rest of the current turn for inspection |

These are automatically wired by `AgentBuilder` 鈥?you don't need to register them.

## Tests

```bash
uv run pytest -q
```

## License

Private.

