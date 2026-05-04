# Phase 5-6 Skills MCP Persistence Observability Design

## Goal

完成 agentos v3 Phase 5 和 Phase 6 的架构设计，使 SDK 在不污染默认 LLM-visible context 的前提下支持：

- Skills 的发现、摘要投影和按需加载。
- MCP server 的注册、工具 schema 暴露和工具调用路由。
- schema template 作为内置 skill/cookbook 延迟分发。
- session snapshot 的文件与 SQLite 持久化和恢复。
- message/context/compression/recall 运行事件的结构化记录。
- Langfuse、OTel 和 debug projection 的可替换观测边界。

这份 spec 的核心目标不是做一个能跑通的薄切片，而是把 Phase 5/6 的验收边界写完整。后续实现只有满足本 spec 的 acceptance checklist、测试矩阵和漂移检查，才能称为 phase complete。

## Design References

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/superpowers/specs/2026-05-03-phase-1-context-mainline-design.md`
- `docs/superpowers/specs/2026-05-03-phase-2-compression-recall-design.md`
- `docs/superpowers/specs/2026-05-03-phase-3-4-small-agent-design.md`
- `../ai-knowledge/wiki/mcp-skills.md`
- `../ai-knowledge/wiki/tool-system.md`
- `../ai-knowledge/wiki/session-recovery.md`
- `../ai-knowledge/wiki/evaluation-observability.md`
- `../ai-knowledge/wiki/runtime-state.md`
- `../neoagent/neoagent/tools/builtin/skill_load.py`
- `../neoagent/neoagent/mcp/client.py`
- `../neoagent/neoagent/session.py`
- `../neoagent/neoagent/integrations/otel.py`

## Scope Contract

Phase 5 acceptance items:

- system prompt only lists skill and MCP summaries.
- a provider tool call loads a skill body on demand.
- MCP tools enter provider tools and the Capability Plane through the same registry source.
- schema template is distributed as a built-in skill/cookbook and is not pre-rendered in the default prompt.

Phase 6 acceptance items:

- a session can be saved and restored with context, messages, active window refs, compression index and event records.
- message/context/compression/recall events are traceable as typed runtime facts.
- Langfuse and OTel adapters are optional import-free adapters around injected clients.
- debug projection can expose runtime metadata explicitly.
- default `ContextRenderer` output still does not expose runtime metadata.

Explicitly out of scope for Phase 5/6:

- Plugin marketplace installation, third-party trust pipeline, and skill package publishing.
- MCP server side implementation.
- Streaming provider responses.
- Redis/Postgres persistence.
- Eval runner and finetuning dataset export.
- Multi-agent orchestration and subagent tool isolation.

## Current Baseline

The repository already has the Phase 1-4 mainline:

- `context/renderer.py` renders the default context sections.
- `context/projection.py` already defines `CapabilityPlane`, `MCPServerDeclaration` and `SkillDeclaration`.
- `capabilities/registry.py` stores provider-callable tools.
- `capabilities/router.py` routes context protocol tools and external tools.
- `runtime/event_bus.py` stores typed runtime events.
- `runtime/query_loop.py` emits lifecycle and tool execution events.
- `messages/`, `compression/` and `recall/` keep original messages, active refs, compressed segments and recall handles.

Phase 5/6 must extend these objects without changing their ownership:

- Capability metadata belongs to `capabilities/` and is projected through `context/projection.py`.
- Default prompt rendering remains in `context/renderer.py`.
- Provider request assembly remains in `runtime/provider_request_builder.py`.
- Query loop remains orchestration only.
- Runtime metadata belongs to `observability/` and `persistence/`, not to default prompt sections.

## Neoagent Reuse Map

Allowed reuse:

- Skill discovery layouts from `skill_load.py`: flat `name.md`, directory `name/SKILL.md`, and `learned/name/SKILL.md`.
- Skill progressive disclosure rule from `skill_load.py`: summaries are visible; full content is returned only by `load_skill`.
- MCP JSON-RPC request id correlation and `tools/list` / `tools/call` semantics from `mcp/client.py`.
- File storage path guard and JSON serialization discipline from `session.py`.
- OTEL subscriber shape from `integrations/otel.py`: convert typed events to spans through an injected tracer.

Rejected reuse:

- Old neoagent prompt renderer, working-memory fields, compressed history schema, and memory context injection model.
- Runtime metadata in default prompt.
- Pydantic/YAML runtime dependencies for Phase 5; agentos currently has no required runtime dependency.
- Old session schema. agentos must serialize v3 context/messages/compression/recall state instead.

## Phase 5 Design

### Capability Plane Source Of Truth

`ToolRegistry` remains the source of truth for provider tool specs and LLM-visible tool summaries. Phase 5 extends the registry to support tool kinds:

```python
ToolKind = Literal["external", "context", "skill", "mcp"]
```

Provider tool specs must include context protocol tools, external tools, skill tools and MCP tools. The default `CapabilityPlane` must be assembled from:

- `ToolRegistry.capability_tool_group(...)` for external tool summaries.
- `SkillRegistry.capability_declarations()` for skill summaries.
- `MCPRegistry.capability_declarations()` for MCP server summaries.

The renderer may display summaries, but it must not display:

- full skill bodies.
- full MCP input schemas.
- MCP tool result payloads.
- `session_id`, `message_id`, `trace_id`, `span_id`, `tool_call_id`, `schema_id`, `projection_id`, `compression_id`, `source`, or `relevance`.

### Skills

New module: `src/agentos/capabilities/skills.py`.

Core objects:

- `SkillDefinition`: immutable skill metadata and full body.
- `SkillRegistry`: discovers, stores and resolves skill definitions.
- `SkillLoadResult`: structured result returned by the skill loader.
- `register_skill_loader_tool(...)`: registers the `load_skill` provider tool in `ToolRegistry`.

Skill fields:

- `name`: provider-callable stable identifier.
- `description`: short human-readable summary.
- `when_to_use`: LLM-visible decision rule.
- `content`: full Markdown instructions returned only by `load_skill`.
- `source`: `builtin`, `filesystem`, or `learned`.
- `path`: optional file path for filesystem skills.

Skill discovery rules:

- Discover `skills_dir/<name>.md`.
- Discover `skills_dir/<name>/SKILL.md`.
- Discover `skills_dir/learned/<name>/SKILL.md`.
- Deduplicate by `name`; earlier source priority wins in this order: builtin, learned, directory skill, flat skill.
- Optional allowlist applies to filesystem skills.
- Learned skills bypass the optional allowlist because they are generated inside the runtime skill directory.
- Missing frontmatter is accepted; the filename becomes `name`, `description` defaults to empty, and `when_to_use` falls back to `description`.
- Frontmatter parsing uses a small stdlib parser for `key: value` fields. No YAML dependency is added.

Skill tool behavior:

- The provider tool name is `load_skill`.
- Arguments schema:

```json
{
  "type": "object",
  "properties": {
    "skill_name": {
      "type": "string",
      "description": "Name of the skill to load."
    }
  },
  "required": ["skill_name"],
  "additionalProperties": false
}
```

- On success, the tool result contains the full skill content and a short header naming the skill.
- On unknown or disallowed skill, the result is a deterministic JSON error string with `error` and `available_skills`.
- The router treats `load_skill` as a normal provider tool result. The full content enters active messages as a tool result, not as a pre-rendered system section.

### Built-In Schema Template Skill

New built-in skill definition: `schema-template`.

Purpose:

- Teach the model how to choose working state schema fields for a task.
- Provide examples for `task_goal`, `constraints`, `key_decisions`, `verified_facts`, `open_questions`, and `next_steps`.
- Explain when to use `declare_schema`, `extend_schema`, `update_state`, and `start_chapter`.

Projection rule:

- The default prompt may list `schema-template` as a skill summary.
- The default prompt must not include the skill body.
- The body is returned only by `load_skill(skill_name="schema-template")`.

### MCP

New module: `src/agentos/capabilities/mcp.py`.

Core objects:

- `MCPToolInfo`: server-local tool name, description, and JSON input schema.
- `MCPClient`: Protocol consumed by agentos. It exposes `list_tools()` and `call_tool(...)`.
- `MCPServerRegistration`: server name, description, optional endpoint, client and optional allowlist.
- `MCPRegistry`: owns MCP server registrations and cached tool metadata.
- `MCPToolAdapter`: maps provider tool names to MCP server calls.

MCP naming:

- Provider tool names use `mcp__<server>__<tool>`.
- `<server>` and `<tool>` are validated with `[A-Za-z0-9_-]+`.
- Name conflicts raise during registration or refresh.

MCP provider schema:

- `MCPRegistry.provider_tool_specs()` returns provider tool specs for all visible MCP tools.
- The function name is the prefixed provider name.
- The description includes the server name and MCP tool description.
- The parameters object comes from MCP `inputSchema`.
- Invalid schemas are normalized to `{"type": "object", "properties": {}, "additionalProperties": true}` and recorded in observability as a warning event.

MCP capability projection:

- `MCPRegistry.capability_declarations()` returns `MCPServerDeclaration` entries, not individual tool schemas.
- The default prompt lists connected server summaries and the prefix rule.
- Full tool schemas stay in provider `tools`, not the default prompt.

MCP execution:

- `ToolCallRouter` detects provider names with `mcp__`.
- `SecurityPolicy.ensure_tool_allowed(...)` runs before any MCP client call.
- `MCPToolAdapter.execute(...)` parses the provider name, calls the registered client, and returns `ToolExecutionResult`.
- MCP client errors become deterministic tool results unless the failure is a programming/configuration error during registration.

The first implementation uses deterministic fake clients in tests. A real stdio JSON-RPC client may be added behind `MCPClient` without changing router or registry contracts.

## Phase 6 Design

### Persistence

New package: `src/agentos/persistence/`.

Core modules:

- `base.py`: snapshot dataclasses and `SessionPersistence` Protocol.
- `serializers.py`: conversions between runtime objects and JSON-safe dicts.
- `memory.py`: in-memory persistence for tests.
- `filesystem.py`: JSON file persistence.
- `sqlite.py`: SQLite persistence using stdlib `sqlite3`.

Snapshot scope:

- `SessionState`: id, status, and next turn number.
- `ContextState`: declared schema, working state, compressed history, inherited state, and memory context.
- `MessageRuntime`: original messages, active refs, temporary flags, and next message id.
- `CompressionIndex`: segment handle to source message refs.
- compression runtime cursor: next segment number.
- `EventLog`: event records for this session.

Serialization rules:

- Snapshot dict contains `version`.
- Unknown snapshot versions raise a clear `SnapshotVersionError`.
- Dataclasses are reconstructed through explicit serializer functions rather than `__dict__`.
- Private mutable fields can be restored only through class methods or constructor arguments added for persistence.
- JSON output uses `ensure_ascii=False` for readable Chinese content.
- File persistence rejects session ids that escape the base directory.
- SQLite persistence stores the latest snapshot and append-only event records in separate tables.

Persistence does not serialize:

- Provider clients.
- MCP client subprocesses or network handles.
- Tool handler callables.
- Hook functions.
- Active file descriptors.

Those objects are rebuilt by the application and then paired with the restored snapshot.

### Session Recovery Flow

Save:

```text
QueryLoop finishes a turn
  -> runtime owner builds SessionSnapshot
  -> persistence.save(snapshot)
  -> EventLog records SnapshotSavedEvent
```

Restore:

```text
persistence.load(session_id)
  -> reconstruct ContextRuntime from ContextState
  -> reconstruct MessageRuntime from MessageStore + ActiveWindow
  -> reconstruct CompressionIndex
  -> create CompressionRuntime with restored index and next segment number
  -> attach provider, tools, skills, MCP clients and observability sinks
```

The restore API must not silently create a blank session when the requested session id does not exist. Missing sessions raise `KeyError`.

### Event Log

New module: `src/agentos/observability/events.py`.

Core objects:

- `EventRecord`: append-only event record with sequence number, event type, session id, turn id, payload, and timestamp.
- `EventLog`: in-memory append-only log and query helpers.
- `EventSubscriber`: Protocol for observation sinks.

EventLog requirements:

- Records typed dataclass events from `runtime/event_bus.py`.
- Preserves event order with monotonic sequence numbers.
- Does not mutate runtime flow.
- Records subscriber errors separately.
- Can export records as JSON-safe dicts for persistence.

Event coverage:

- Existing lifecycle events remain typed dataclasses.
- Message append events include `message_id`.
- Tool events include `tool_name` and `tool_call_id`.
- Context events cover schema declaration, state update, schema extension, chapter start, inherited state set, memory context set, and compressed segment append.
- Compression events cover compression skipped, compression completed, and source refs removed.
- Recall events cover recall requested, recall failed, and temporary refs injected.
- Persistence events cover snapshot saved and snapshot loaded.

### Tracing And Adapters

New modules:

- `src/agentos/observability/traces.py`
- `src/agentos/observability/otel.py`
- `src/agentos/observability/langfuse.py`

Tracing model:

- `TraceRecord` is an internal normalized trace item.
- `TraceSink` is a Protocol with `record(record: TraceRecord) -> None`.
- `EventTraceProjector` converts selected `EventRecord` items into trace records.

Adapter rules:

- Core agentos keeps zero runtime dependencies.
- `OTelAdapter` accepts an injected tracer-like object.
- `LangfuseAdapter` accepts an injected client-like object.
- Tests use deterministic fake tracer/client objects.
- Adapters record event type, ids, tool name, segment id, status, and token usage when present.
- Adapters do not put full prompt or message content into span attributes by default.

### Debug Projection

New module: `src/agentos/context/debug_projection.py`.

Purpose:

- Provide an explicit debug-only view that can include runtime metadata.
- Help inspect session recovery, compression indexes and event records.

Inputs:

- `ContextState`
- `MessageRuntime`
- `CompressionIndex`
- `EventLog`

Output:

- A deterministic Markdown string with sections for context state, active refs, compression index and recent events.

Boundary:

- `ContextRenderer.render(...)` remains the only default prompt renderer.
- Debug projection is never called by `ProviderRequestBuilder`.
- Golden tests must prove the default renderer still omits runtime metadata while debug projection includes it explicitly.

## Public API

Public imports must use lowercase `agentos`.

New public names:

- `agentos.capabilities.SkillDefinition`
- `agentos.capabilities.SkillRegistry`
- `agentos.capabilities.SkillLoadResult`
- `agentos.capabilities.MCPToolInfo`
- `agentos.capabilities.MCPClient`
- `agentos.capabilities.MCPRegistry`
- `agentos.capabilities.MCPToolAdapter`
- `agentos.persistence.SessionSnapshot`
- `agentos.persistence.SessionPersistence`
- `agentos.persistence.MemoryPersistence`
- `agentos.persistence.FileSystemPersistence`
- `agentos.persistence.SQLitePersistence`
- `agentos.observability.EventLog`
- `agentos.observability.EventRecord`
- `agentos.observability.TraceRecord`
- `agentos.observability.EventTraceProjector`
- `agentos.observability.OTelAdapter`
- `agentos.observability.LangfuseAdapter`

Architecture tests must reject old mixed-case package imports and accidental snake-case package aliases.

## Test Matrix

Phase 5 tests:

- `tests/capabilities/test_skills.py`
- `tests/capabilities/test_mcp.py`
- `tests/capabilities/test_tool_registry_phase5.py`
- `tests/context/test_capability_plane_phase5.py`
- `tests/runtime/test_skill_mcp_tool_loop.py`

Phase 6 tests:

- `tests/persistence/test_serializers.py`
- `tests/persistence/test_filesystem.py`
- `tests/persistence/test_sqlite.py`
- `tests/observability/test_event_log.py`
- `tests/observability/test_traces.py`
- `tests/context/test_debug_projection.py`
- `tests/runtime/test_session_recovery.py`
- `tests/architecture/test_public_api.py`

Required verification commands:

```bash
uv run --python 3.11 --extra dev pytest -q
uv run --python 3.11 --extra dev python -m compileall -q src tests
git diff --check
rg -n "agent[O]s|agent[_]os" src tests docs pyproject.toml README.md
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance" tests/context/goldens src/agentos/context/renderer.py
```

The last command is expected to report only intentional tests that assert forbidden metadata is absent, or debug projection tests that are explicitly not default prompt tests.

## Acceptance Checklist

| Requirement | Implementation files | Test files | Status |
|---|---|---|---|
| Skill summaries render without skill bodies. | `capabilities/skills.py`, `context/renderer.py` | `tests/capabilities/test_skills.py`, `tests/context/test_capability_plane_phase5.py` | required |
| `load_skill` returns full content only through tool result. | `capabilities/skills.py`, `capabilities/router.py` | `tests/runtime/test_skill_mcp_tool_loop.py` | required |
| Schema template is a built-in skill and not pre-rendered. | `capabilities/skills.py` | `tests/capabilities/test_skills.py`, `tests/context/test_capability_plane_phase5.py` | required |
| MCP tools produce provider schemas and server summaries from one registry. | `capabilities/mcp.py`, `capabilities/registry.py` | `tests/capabilities/test_mcp.py`, `tests/capabilities/test_tool_registry_phase5.py` | required |
| MCP provider tool calls route through `ToolCallRouter` and `SecurityPolicy`. | `capabilities/mcp.py`, `capabilities/router.py`, `policies/security.py` | `tests/capabilities/test_mcp.py`, `tests/runtime/test_skill_mcp_tool_loop.py` | required |
| Session snapshot restores context, messages, active refs and compression index. | `persistence/base.py`, `persistence/serializers.py`, `persistence/memory.py` | `tests/persistence/test_serializers.py`, `tests/runtime/test_session_recovery.py` | required |
| File persistence rejects path traversal and round-trips JSON snapshots. | `persistence/filesystem.py` | `tests/persistence/test_filesystem.py` | required |
| SQLite persistence stores latest snapshot and append-only event records. | `persistence/sqlite.py` | `tests/persistence/test_sqlite.py` | required |
| message/context/compression/recall events are traceable. | `runtime/event_bus.py`, `context/runtime.py`, `compression/runtime.py`, `recall/runtime.py`, `observability/events.py` | `tests/observability/test_event_log.py`, `tests/runtime/test_session_recovery.py` | required |
| Langfuse and OTel adapters are import-free and use injected clients. | `observability/langfuse.py`, `observability/otel.py` | `tests/observability/test_traces.py` | required |
| Debug projection exposes metadata only through explicit API. | `context/debug_projection.py`, `context/renderer.py` | `tests/context/test_debug_projection.py`, `tests/context/test_renderer.py` | required |
| Public API exports Phase 5/6 names under `agentos`. | package `__init__.py` files | `tests/architecture/test_public_api.py` | required |

Phase 5/6 are complete only when every row is implemented, tested, and passes the required verification commands.
