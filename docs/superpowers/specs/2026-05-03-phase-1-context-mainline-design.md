# Phase 1 Context Mainline Design

## Status

This spec is written after Task 1 and Task 2 were implemented. It records the current baseline honestly and governs Task 3 onward.

## Design References

- `docs/design/llm-context-only-example.md`
- `docs/design/sdk-architecture.md`
- `AGENTS.md`
- `ai-knowledge/wiki/tool-system.md`
- `ai-knowledge/wiki/mcp-skills.md`
- `ai-knowledge/ideas/2026-05-02-neoagent-context-protocol-v3.md`

## Phase 1 Goal

Build the first context-first SDK mainline:

```text
ContextState
-> ContextRenderer
-> context protocol tools
-> MessageStore / ActiveWindow
-> ProviderRequestBuilder
-> FakeProvider loop
```

The first phase must keep the SDK designed from the LLM-visible context outward. Provider adapters, MCP execution, multi-agent, persistence, and full observability are outside Phase 1.

## Completed Baseline

### Task 1: Project Initialization

The project now has:

- `pyproject.toml`
- `uv.lock`
- Python `>=3.11`
- pytest dev dependency
- `src/agentos`
- `tests/context`
- initialized Git repository

Task 1 is complete when:

```bash
uv run --python 3.11 --extra dev pytest -q
```

runs the local test suite.

### Task 2: Context Renderer

The context renderer baseline now includes:

- `ContextState`
- `WorkingStateSchema`
- `WorkingStateField`
- `CompressedSegment`
- `RuntimeContract`
- `CapabilityPlane`
- `ToolGroup`
- `ToolDeclaration`
- `MCPServerDeclaration`
- `SkillDeclaration`
- `ContextRenderer`
- default golden context projection

The default LLM-visible section order is:

```text
Runtime Contract
Capability Plane
Context Management Rules
Declared Working State Schema
Working State
Compressed History
Memory Context
```

The renderer supports project customization through structured projection inputs:

```python
ContextRenderer(
    runtime_contract=RuntimeContract(
        identity="你是一个专注 Agent OS SDK 的工程助手。",
        extra_guardrails=[
            "所有文件修改必须保持 context 模块边界。",
        ],
    ),
    capability_plane=CapabilityPlane(
        tool_groups=[...],
        mcp_servers=[...],
        skills=[...],
    ),
)
```

Capability Plane follows the ai-knowledge register projection shape:

- Tools are rendered by group.
- Full tool schemas are passed through provider `tools`, not rendered into system prompt.
- MCP servers are rendered by server summary and tool prefix.
- Skills are rendered as frontmatter / when-to-use summaries and loaded through `Skill`.

Golden tests assert:

- section order is stable
- context protocol tools appear
- non-default context tools do not appear
- chapter granularity is explained
- runtime metadata does not appear in default prompt
- declared schema field order is preserved
- project Runtime Contract customization works
- project Capability Plane injection works

## Task 3 Scope: Context Runtime Tools

Task 3 adds the minimal runtime API that applies context protocol tools to `ContextState`.

Create:

- `src/agentos/context/runtime.py`
- `tests/context/test_runtime.py`

Modify:

- `src/agentos/context/__init__.py`

## Task 3 Responsibilities

`ContextRuntime` owns context tool effects only. It may mutate `ContextState`; it must not know provider messages, execute external tools, compress messages, or write observability metadata.

Task 3 context protocol APIs:

```python
declare_schema(fields: list[WorkingStateField]) -> None
update_state(field_name: str, value: WorkingStateValue) -> None
extend_schema(fields: list[WorkingStateField]) -> None
start_chapter(fields: list[WorkingStateField] | None = None) -> None
```

Task 3 must not add default APIs for:

- `read_state`
- `abort_chapter`
- `mark_important`

## Task 3 Behavior

### `declare_schema`

- Can be called only when the current chapter has no declared schema.
- Requires at least one field.
- Rejects duplicate field names.
- Rejects empty field names, types, or purposes.
- Preserves field order exactly as provided.
- Does not initialize hidden runtime metadata.

### `update_state`

- Requires an already declared schema.
- Requires `field_name` to exist in the declared schema.
- Writes the provided value into `ContextState.working_state`.
- Does not append event history.
- Does not mutate the schema.

### `extend_schema`

- Requires an already declared schema.
- Requires at least one new field.
- Rejects duplicates within the new fields.
- Rejects field names that already exist in the current schema.
- Appends fields in the order provided.
- Does not rewrite existing fields or working state values.

### `start_chapter`

- Clears current working state.
- Replaces current schema with the provided fields, or with an empty schema if no fields are provided.
- Keeps `compressed_history` and `memory_context`.
- Does not render inherited state yet. Inherited state remains a documented projection variant for a later task.

## Error Handling

Invalid context tool calls raise `ContextProtocolError`.

This is a local SDK error type under `context/`. It is not a provider error, not a message runtime error, and not an observability event.

## Testing

Task 3 tests must cover:

- declaring a schema preserves field order
- declaring twice in one chapter fails
- updating a declared field succeeds
- updating an undeclared field fails
- extending schema appends fields and preserves existing values
- extending with duplicate or existing field names fails
- starting a chapter resets schema and working state while preserving M3 projections
- `read_state`, `abort_chapter`, and `mark_important` are not exposed as default context runtime APIs

## Out Of Scope

Task 3 does not implement:

- MessageStore
- ActiveWindow
- compression
- recall message injection
- provider request building
- external tool execution
- MCP client lifecycle
- skill loading
- persistence
- observability adapters

## Self Review

- No placeholders remain.
- Task 1 and Task 2 are recorded as completed baseline, not represented as pre-existing plans.
- Task 3 stays inside the `context/` module boundary.
- Runtime metadata remains outside the default prompt.
- Inherited state is not silently added to the default seven-section context shape.
