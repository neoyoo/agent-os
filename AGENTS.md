# Agent OS Development Instructions

These instructions apply to the `agent-os` project. They are intentionally stricter than generic Python project rules because this SDK is being rebuilt around a new context-first architecture.

## Project Mission

Agent OS is a Python agent runtime SDK built around a context-first architecture.

Core principle:

```text
Context protocol 决定 agent 的认知模型。
ai-knowledge 模块体系决定 SDK 的工程骨架。
旧 neoagent 代码只作为局部实现参考，不作为架构约束。
```

This project is a clean rewrite. Do not preserve old neoagent abstractions unless they fit the new architecture.

## Required Design References

Before implementing architecture-level behavior, read these files:

- `docs/design/llm-context-only-example.md`
- `docs/design/sdk-architecture.md`

The context example is the golden target for what the LLM should see. The SDK architecture document is the module boundary map.

## Architecture Rules

### Context Is The Cognition Model

The SDK must be designed from the LLM-visible context outward.

The default LLM-visible context shape is:

```text
Runtime Contract
Capability Plane
Context Management Rules
Declared Working State Schema
Working State
Compressed History
Memory Context
```

The SDK exists to maintain and render that context safely.

### ai-knowledge Is The Engineering Skeleton

Use the ai-knowledge module taxonomy as the SDK skeleton:

- `query-loop` -> `runtime/query_loop.py`
- `runtime-state` -> `runtime/session.py`, `runtime/turn.py`
- `context-management` -> `context/`, `messages/`, `compression/`, `recall/`
- `prompt-system` -> `context/renderer.py`, `context/projection.py`
- `tool-system` -> `capabilities/tools.py`, `capabilities/executor.py`
- `memory-system` -> `memory/`
- `mcp-skills` -> `capabilities/mcp.py`, `capabilities/skills.py`
- `multi-agent` -> `multi/`
- `agent-registry-discovery` -> `registry/`, `multi/registry.py`, `channels/`
- `hooks` -> `events/`, `hooks/`
- `session-recovery` -> `persistence/`, `runtime/session.py`
- `evaluation-observability` -> `observability/`, `eval/`
- `finetuning-system` -> `finetuning/`, `eval/`, `observability/`
- `channel-remote` -> `channels/`
- `sandbox-isolation` -> `policies/security.py`, `capabilities/executor.py`

Do not let old neoagent package names or old APIs override these boundaries.

### Naming Discipline

Naming is part of the architecture. Names must communicate ownership and runtime responsibility clearly enough that a new contributor can understand the object graph before reading method bodies.

Project naming rules:

- The public Python import package is `agentos`, following PEP 8 lowercase package naming.
- The distribution/project name may remain installer-friendly, but Python code and docs that show imports must use `agentos`.
- Existing `neoagent` names are only a naming-style reference. Do not bring back old prompt, working-memory, or compression abstractions unless they fit the v3 context-first architecture.
- Class and function docstrings should be maintained in Chinese for project-owned code, with protocol identifiers left in English.

Class naming rules:

- Prefer concrete responsibility names over vague `Runtime` names.
- Use `Runtime` only for a long-lived subsystem object that owns mutable lifecycle state and coordinates a bounded domain, such as `ContextRuntime`, `MessageRuntime`, `CompressionRuntime`, or `RecallRuntime`.
- Use `Loop` only for the agent execution loop. The main loop should be named `QueryLoop`, matching the ai-knowledge `query-loop` concept.
- Use `Builder` only for objects that assemble a value without owning lifecycle state. The provider request assembler is `ProviderRequestBuilder`.
- Use `Provider` for model backends. The provider protocol is `Provider`.
- Use `Registry` only for name-to-object or name-to-schema registration, such as `ToolRegistry`.
- Use `Executor` only for actually running tools or side-effecting work, such as `ToolExecutor`.
- Use `Router` for dispatching a provider tool call to the correct executor or context tool. The tool-call dispatcher is `ToolCallRouter`.
- Use `Manager` for policy-like coordination that can allow, deny, or modify behavior, such as `HookManager`.
- Use `Bus` only for pub/sub observation. `EventBus` must not intercept or modify execution.

Event and hook naming rules:

- Runtime events must be typed dataclasses, not loose string names in a generic event object.
- Event classes must be named by the thing that happened: `TurnStartedEvent`, `UserMessageAppendedEvent`, `ContextRenderedEvent`, `ProviderRequestBuiltEvent`, `ProviderResponseReceivedEvent`, `AssistantMessageAppendedEvent`, `ToolCallRequestedEvent`, `ToolExecutionStartedEvent`, `ToolExecutionCompletedEvent`, `ToolResultAppendedEvent`, `TurnCompletedEvent`, `TurnFailedEvent`.
- `EventBus` is observation-only. A handler exception may be recorded, but event handlers must not mutate execution flow.
- Hook interception belongs to `HookManager`, with explicit pre/post hook event types and `HookResult` actions such as allow, deny, and modify.
- Do not mix tracing, observability, and hook interception into the same class.

Required public names:

| Responsibility | Required name | Reason |
|---|---|---|
| Query loop | `QueryLoop` | Owns the query/turn execution loop. |
| Provider request assembly | `ProviderRequestBuilder` | Builds provider-facing requests only. |
| Model backend protocol | `Provider` | Defines the model backend boundary. |
| Tool-call dispatch | `ToolCallRouter` | Routes provider tool calls to context tools or external tool execution. |
| Hook policy coordination | `HookManager` | Manages hook registration and interception policy. |
| Runtime lifecycle facts | typed `*Event` classes | Makes lifecycle events discoverable and testable. |

## Completion Discipline

Do not treat a runnable MVP as a finished phase. A phase is complete only when
its design/spec acceptance items, naming rules, module boundaries, and tests are
all satisfied.

Before implementing non-trivial SDK behavior, write a short Scope Contract in
the working notes or user-facing update:

1. Which phase/spec this task belongs to.
2. Which acceptance items apply.
3. Which items this change will complete.
4. Which items are intentionally deferred, and to which later phase.
5. Any design rule that would be violated if the implementation is simplified.

Silent deferral is not allowed. If implementation is below the design target,
say so explicitly and mark the missing item as deferred. Do not claim that a
phase or task is complete when any acceptance item is still missing.

Before saying a phase, task, or architecture-level change is complete, produce a
checklist:

- Design requirement.
- Implementation file(s).
- Test file(s) or verification command.
- Status: complete, deferred, or not applicable.

If any row is deferred or incomplete, the final answer must say "partially
complete" rather than "complete".

Architecture-level work must re-read or inspect these references before code
changes:

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- the active `docs/superpowers/specs/...` or `docs/superpowers/plans/...`
- relevant `ai-knowledge/wiki/...` pages for the touched module

Do not bypass already-decided architecture rules with "simple first"
implementations. In particular:

- Do not introduce loose string runtime events when typed event dataclasses are
  required.
- Do not use unclear names such as generic `Runtime`, `Manager`, or
  `ProviderRequestBuilder` when a responsibility-specific name is already specified.
- Do not mix observation events, hook interception, and observability traces in
  one class.
- Do not render fake capability entries. LLM-visible capability projection must
  come from the same registry/source of truth that powers provider schemas and
  execution routing.
- Do not say "done" based only on a working demo; demos prove a path, not SDK
  completeness.

Required completion checks for architecture-level changes:

- targeted tests for the changed behavior
- full test suite
- `python -m compileall -q src tests`
- `git diff --check`
- drift search for forbidden old names or missing target names when a rename or
  architecture vocabulary change is involved

## Module Boundaries

### Runtime

`runtime/query_loop.py` owns turn orchestration only.

It may:

- accept user input
- append messages through MessageRuntime
- ask ContextRuntime to prepare/render context
- ask ProviderRequestBuilder to build provider requests
- call Provider
- route tool calls through ToolCallRouter
- repeat until final assistant response

It must not:

- directly mutate working state
- directly compress messages
- directly concatenate prompt strings
- directly execute concrete tools
- directly write observability metadata

### Context

`context/` owns the agent cognition model.

It may:

- manage `ContextState`
- manage declared working state schema
- manage working state updates
- render LLM-visible context projection
- store compressed history projection state
- store inherited state and memory context projection state
- execute context tools through explicit APIs

It must not:

- store provider original messages
- execute external tools
- manage provider calls
- render SDK runtime metadata into the default prompt

### Messages

`messages/` owns the message truth source and active window.

It may:

- append original messages
- maintain active message refs
- materialize active provider messages
- protect tool_use/tool_result pairs
- inject temporary recalled messages

It must not:

- delete original messages when compressing
- summarize content itself
- mutate working state

### Compression And Recall

Compression must follow this sequence:

```text
select active message refs
read original messages from MessageStore
produce compressed segment
append segment to ContextState
remove selected refs from ActiveWindow
keep original messages in MessageStore
```

Recall must follow this sequence:

```text
recall_context(handle="seg_1")
lookup segment source refs
restore original messages from MessageStore
inject temporary recalled messages
auto-remove temporary recalled messages after the next request
```

### Capabilities

`capabilities/` owns tools, skills, MCP, and subagent tool routing.

Context tools are capabilities, but their effects are applied by ContextRuntime.

Skills and MCP belong to the Capability Plane, not to context projection.

### Providers

`providers/` must not know context internals.

Provider input is:

```text
system: rendered context
messages: active messages
tools: provider tool schemas
```

### Observability

`observability/` is the home for runtime metadata.

Runtime metadata includes:

- `session_id`
- `turn_id`
- `message_id`
- `trace_id`
- `span_id`
- `tool_call_id`
- `schema_id`
- `projection_id`
- `compression_id`
- provider usage
- budget events
- recall events
- tool execution events

Default LLM prompts must not include runtime metadata. Debug projection may expose it explicitly.

## Context Protocol Tools

The default LLM-visible context tools are:

```text
declare_schema
update_state
extend_schema
start_chapter
recall_context
```

Do not add `read_state`, `abort_chapter`, or `mark_important` as default LLM-visible tools in the first implementation. They can be debug/ops tools later.

## Old neoagent Reuse Rules

You may copy or adapt old neoagent code for:

- provider API details
- tool calling schema adaptation
- MCP client lifecycle management
- skill discovery mechanics
- configuration loading
- event/observer implementation ideas
- tests and fixtures

Do not copy or preserve:

- old 8-layer prompt renderer
- old working memory field model
- old PromptBuilder string-concatenation abstraction
- old compressed history schema
- old memory context injection model
- logic that renders runtime metadata into the default prompt

## Python Development Rules

Use these rules unless a stronger local tool or user instruction overrides them.

- Prefer Python 3.11+ syntax and type annotations.
- Keep modules focused and small.
- Use dataclasses or Pydantic models only when they simplify validation or boundaries.
- Avoid framework-wide abstractions until two real call sites need them.
- Keep public APIs typed.
- Keep side effects explicit.
- Avoid hidden global state.
- Do not perform network calls in tests unless explicitly marked integration.
- Use deterministic fakes for provider, compressor, persistence, and tools.

## Testing Rules

Use test-first development for SDK behavior.

Minimum early test suites:

- renderer golden/context structure tests
- working state tool tests
- message store and active window tests
- compression and recall tests
- request builder tests
- runtime loop tests with fake provider

Every behavior should have a local unit or integration test before implementation.

Golden tests must assert that default prompts do not include runtime metadata such as `session_id`, `message_id`, `trace_id`, `schema_id`, `compression_id`, `source`, or `relevance`.

## First Implementation Target

The first runnable vertical slice should support:

```text
user message
-> MessageRuntime append
-> declare_schema / update_state
-> ContextRuntime render
-> ProviderRequestBuilder build ProviderRequest
-> FakeProvider response
-> compression into seg_1
-> recall_context("seg_1")
```

Do not start with MCP, multi-agent, remote channels, or full observability adapters. Those are later modules.

## Communication For Future Agents

When working in this repo:

- State which design reference you used.
- State which module boundary your change touches.
- If you need to violate a boundary, stop and explain why before editing.
- Keep changes scoped to the active task.
- Do not import old neoagent code without naming exactly what is being reused and why it fits the new boundary.
