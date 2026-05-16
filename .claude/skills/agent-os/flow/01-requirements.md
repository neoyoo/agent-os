---
name: agent-os-requirements
description: Phased requirements gathering for agent-os SDK projects — asks 6 dimensions one at a time, outputs structured decisions for spec generation
---

# Requirements Gathering

## Rules

<HARD-GATE>
1. Present ONE dimension at a time. Wait for user response before proceeding.
2. For each dimension, explain what it means in agent-os terms, offer concrete options, and state the default.
3. After all 6 dimensions, summarize decisions in a table and ask for final confirmation.
4. Do NOT generate code or spec until user confirms the summary.
5. ALWAYS respond in the user's language. The templates below are English reference — translate and adapt when presenting to the user. If the user writes in Chinese, all questions, options, and explanations MUST be in Chinese.
</HARD-GATE>

## Pre-Check

Before asking dimensions, check if the user's description maps to a known agent form in `modules/agent-forms.md`. If it does, mention the form name and which modules it uses — this helps them understand what the SDK already handles vs. what they need to customize.

## Dimensions

### 【1/6】Agent Purpose & Provider

**Ask:**
- What problem does this agent solve? (one sentence)
- Which LLM provider? Options:
  - `AnthropicProvider` — Claude models (recommended for tool-use)
  - `OpenAIProvider` — GPT-4o / o1 models
  - `OpenAICompatibleProvider` — any OpenAI-compatible endpoint (Ollama, vLLM, etc.)
- Model name? (e.g. `claude-sonnet-4-6`, `gpt-4o`)

**Default:** AnthropicProvider + claude-sonnet-4-6

---

### 【2/6】Deployment & State

**Ask:**
- Where does this agent run?
  - **Local** — single process, in-memory state, dev/CLI use
  - **Server (single node)** — HTTP API, state persists to disk (SQLite/FileSystem)
  - **Server (multi-node)** — stateless workers, state in Redis (hot) + Postgres (durable)

**What this decides:**
| Mode | SessionProvider | Persistence | State between turns |
|------|----------------|-------------|---------------------|
| Local | in-memory | none | held in Agent object |
| Single node | in-memory + snapshot | SQLitePersistence / FileSystemPersistence | save/load between restarts |
| Multi-node | RedisHotSessionStore + PostgresDurableSessionStore | full pipeline | load from Redis per request, save back after turn |

**Default:** Local (simplest path, can upgrade later)

---

### 【3/6】Tools & Capabilities

**Ask:**
- What actions can the agent perform? List the tools it needs.
  - Each tool = a `RegisteredTool(name, description, parameters, handler)`
  - Examples: file_read, web_search, browser_navigate, db_query, api_call, code_execute
- Any MCP servers to connect? (MCP = Model Context Protocol, for external tool hosts)

**What this decides:**
- `AgentBuilder.tools([...])` — list of RegisteredTool
- `ToolCallRouter` — wired automatically by builder
- MCP → `MCPToolAdapter` if needed

**Note:** Context protocol tools (declare_schema, update_state, extend_schema, start_chapter, recall_context) are ALWAYS available — they're wired by default in AgentBuilder. You don't need to declare them.

**Default:** No external tools (agent can still use context protocol tools)

---

### 【4/6】Context & Compression Strategy

**Ask:**
- How long are typical sessions?
  - **Short** (< 20 messages) — no compression needed
  - **Medium** (20-100 messages) — rule-based compression recommended
  - **Long** (100+ messages) — LLM-based compression for quality summaries
- Does the agent need structured working state? (e.g., tracking goals, progress, discovered facts)
  - If yes: model will use `declare_schema` / `update_state` to maintain structured state
  - If no: context protocol tools still available, model decides when to use them

**What this decides:**
- `AgentBuilder.with_compression(compressor)` — enables CompressionRuntime
- `RuleBasedCompressor` (default) vs `LlmCompressor` (needs extra provider calls)
- `FallbackCompressor(primary=LlmCompressor(...), fallback=RuleBasedCompressor())` for reliability

**Default:** No compression (short sessions). Context protocol always on.

---

### 【5/6】Multi-Agent

**Ask:**
- Is this a single agent or does it coordinate with others?
  - **Single** — one agent, one loop
  - **Local sub-agents** — spawns child agents in threads (same process)
  - **Distributed (A2A)** — communicates with remote agents via A2A protocol over HTTP

**What this decides:**
| Mode | SDK module | Coordination |
|------|-----------|--------------|
| Single | just Agent | — |
| Local spawn | `multi/coordinator.py` + `multi/spawn.py` | in-process, shared trace context |
| Distributed | `channels/a2a.py` + `channels/a2a_server.py` | HTTP, W3C trace propagation |

**Default:** Single agent

---

### 【6/6】Channel & Access

**Ask:**
- How do users/systems interact with this agent?
  - **Programmatic** — imported as library, called via `agent.run()` / `agent.stream()`
  - **HTTP API** — exposed via ASGI app, POST `/v1/sessions/{id}/turns`
  - **HTTP + SSE streaming** — same + streaming endpoint `/v1/sessions/{id}/turns/stream`
  - **A2A server** — accepts tasks from other agents via A2A protocol

**What this decides:**
- No channel → use Agent directly
- HTTP → `AsgiAgentApp` + `AgentSessionProvider`
- A2A → additionally wire `A2AServer` into the ASGI app

**Default:** Programmatic (library use)

---

## Summary Template

After all 6 dimensions, present:

```
┌─────────────────────────────────────────────────┐
│ Agent: [one-line description]                    │
├─────────────────────────────────────────────────┤
│ Provider:     [AnthropicProvider / claude-sonnet-4-6]   │
│ Deployment:   [local / single-node / multi-node]        │
│ Tools:        [list or "context-only"]                  │
│ Compression:  [none / rule-based / llm-based]           │
│ Multi-agent:  [single / local-spawn / a2a]              │
│ Channel:      [programmatic / http / http+sse / a2a]    │
└─────────────────────────────────────────────────┘
```

Ask: "确认这些选择？我来生成 spec。" → proceed to `flow/02-spec-generation.md`
