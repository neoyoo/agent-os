---
name: agent-os-spec-generation
description: Generate agent-spec.yaml from confirmed requirements — produces a complete blueprint that subagents can implement independently
---

# Spec Generation

## Prerequisites

User has confirmed the 6-dimension summary from `flow/01-requirements.md`.

## Output

Generate `specs/agent-spec.yaml` in the target project directory. This file is the single source of truth for implementation.

## Spec Schema

```yaml
# specs/agent-spec.yaml
version: 1
name: <agent-name>
description: <one-line purpose>

provider:
  type: AnthropicProvider | OpenAIProvider | OpenAICompatibleProvider
  model: <model-id>
  max_tokens: <number>
  # extra provider-specific config
  base_url: <only for OpenAICompatible>

deployment:
  mode: local | single-node | multi-node
  # only for single-node / multi-node:
  persistence:
    hot_store: redis  # only multi-node
    hot_store_url: redis://localhost:6379
    hot_store_ttl: 3600
    durable_store: postgres | sqlite | filesystem
    durable_store_dsn: <connection string>  # postgres
    durable_store_path: <directory>  # sqlite / filesystem

tools:
  # each tool becomes a RegisteredTool
  - name: <tool-name>
    description: <LLM-facing description — precise, unambiguous>
    parameters:
      type: object
      properties:
        <param>:
          type: string
          description: <what this param does>
      required: [<param>]
    handler: <module.path:function_name>
    # handler is a sync function: (arguments: dict) -> str

  # MCP servers (optional)
  mcp_servers:
    - name: <server-name>
      command: [<cmd>, <args...>]
      env:
        KEY: value

context:
  compression:
    enabled: true | false
    strategy: rule-based | llm-based | fallback
    # fallback = LlmCompressor primary + RuleBasedCompressor secondary
    budget:
      max_active_messages: 20
      retain_latest_messages: 6
  working_state:
    # pre-declared schema (optional — model can also declare at runtime)
    initial_schema:
      - name: <field>
        type: <str | list[str] | dict>
        purpose: <when to update this field>

multi_agent:
  mode: single | local-spawn | a2a
  # only for local-spawn:
  workers:
    - name: <worker-name>
      description: <what this worker does>
      tools: [<tool-names>]
      model: <optional model override>
  # only for a2a:
  remote_agents:
    - name: <agent-name>
      endpoint: <url>
      description: <capability description>

channel:
  type: programmatic | http | http+sse | a2a-server
  # only for http variants:
  host: "0.0.0.0"
  port: 8000
  auth:
    type: none | bearer | custom
    # bearer:
    token_env: AUTH_TOKEN
  # only for a2a-server (adds /a2a/tasks endpoint):
  a2a:
    agent_card:
      name: <name>
      description: <capability>
      skills: [<skill-names>]

hooks:
  # lifecycle hooks to wire
  - point: before_provider_call | after_provider_call | before_tool_call | after_tool_call
    handler: <module.path:function_name>
    priority: 100  # lower = runs first
    purpose: <what this hook does>

observability:
  tracing: true | false  # W3C trace context propagation
  event_bus: true | false  # typed event emission
```

## Generation Rules

1. Only include sections relevant to the user's choices. Don't generate `multi_agent` section for single-agent mode.
2. Tool descriptions must be LLM-facing prompts — precise and unambiguous. They go directly into the provider request.
3. Handler paths must be resolvable. Use `<project>.tools.<name>:handle_<name>` pattern.
4. For multi-node deployment, ALWAYS include both hot_store and durable_store.
5. For compression, default budget is `max_active_messages=20, retain_latest_messages=6`.

## After Generation

Present the spec to the user. Ask:
- "Spec 看起来对吗？有需要调整的吗？"
- Once confirmed → proceed to `flow/03-implementation.md`
