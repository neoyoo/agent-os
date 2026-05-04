# Phase 3-4 Small Agent Design

## Goal

Complete Phase 3 and Phase 4 enough to run a small tool-using agent end to end with a deterministic fake provider.

## Design References

- `docs/design/llm-context-only-example.md`
- `docs/design/sdk-architecture.md`
- `AGENTS.md`

## Scope

Phase 3 adds runtime lifecycle state and hook dispatch:

- `SessionState` owns session id, status, and turn counter.
- `TurnState` owns per-turn id, user input, status, and tool iteration count.
- `EventBus` records typed runtime events and notifies observation subscribers only.
- `HookRegistry` stores ordered hook handlers per explicit hook point.
- `HookManager` executes hooks with an explicit failure policy and `HookResult`.

Phase 4 adds the minimal tool system and provider tool-call loop:

- `ProviderResponse` can carry normalized tool calls.
- `FakeProvider` can return either text or full `ProviderResponse` objects.
- `ToolRegistry` registers tool declarations and provider schemas.
- `ToolExecutor` checks `SecurityPolicy`, calls handlers, and returns tool results.
- `ToolCallRouter` routes provider tool calls to external tools or context tools.
- `QueryLoop` repeats provider calls while tool calls are returned, appending assistant tool calls and tool results to `MessageRuntime`.
- Lightweight OpenAI and Anthropic provider adapters are import-free and accept injected clients; network integration tests are out of scope.

## Out Of Scope

- Real OpenAI/Anthropic network calls in tests.
- Streaming.
- MCP and Skills.
- Persistence and observability adapters.
- Human approval prompts for tool permissions.

## Small Agent Acceptance

The repo must include a deterministic integration test for this workflow:

```text
User asks for the project name
  -> FakeProvider returns a read_file tool call for pyproject.toml
  -> ToolCallRouter executes read_file through ToolExecutor
  -> MessageRuntime appends the tool result
  -> FakeProvider receives the tool result in the next request
  -> FakeProvider returns the final answer "agent-os"
```

Additional acceptance:

- Runtime events are emitted for turn start, request build, provider response, tool execution, tool result append, and turn completion.
- Hooks execute in registration order at explicit hook points, not through runtime event logging.
- Hook failures follow an explicit policy.
- Default prompts still do not include runtime metadata.
- Security policy can deny a tool before its handler runs.
