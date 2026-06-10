# agent-os SDK Iteration Input From ai-knowledge Refresh

> Date: 2026-06-10  
> Source: `/Users/neo/Desktop/project/git/ai-knowledge` wiki refresh after syncing current open-source agent projects.  
> Purpose: handoff document for the next agent-os SDK iteration window.

## Scope Contract

This document is not an implementation spec. It is the design input distilled from the latest ai-knowledge update.

The next agent-os iteration should focus on one product requirement:

```text
agent-os must support two agent deployment shapes:
1. Local proxy agent: local process, local tools, local workspace, local memory.
2. Web distributed agent: remote channel, distributed workers, registry/discovery, durable sessions, resumable events.
```

The SDK should express this through stable abstract contracts first, then provide separate local and distributed implementations. Do not hardcode either shape into `QueryLoop`, `ContextRuntime`, `MemoryRuntime`, or the channel layer.

## Required References For The New Window

Read these before changing code:

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/superpowers/specs/2026-05-28-tool-result-token-budget-design.md`
- `docs/superpowers/specs/2026-05-28-token-aware-compression-circuit-breaker-design.md`
- `docs/superpowers/specs/2026-05-28-sse-resume-mid-turn-design.md`
- `ai-knowledge/docs/open-source-agent-project-highlights-2026-06-09.md`
- `ai-knowledge/wiki/memory-system.md`
- `ai-knowledge/wiki/context-management.md`
- `ai-knowledge/wiki/channel-remote.md`
- `ai-knowledge/wiki/session-recovery.md`
- `ai-knowledge/wiki/multi-agent.md`
- `ai-knowledge/wiki/agent-registry-discovery.md`
- `ai-knowledge/wiki/sandbox-isolation.md`
- `ai-knowledge/wiki/_patterns/symbolic-context-map-progressive-recall.md`

## High-Level Direction

The current agent-os architecture is already aligned with the ai-knowledge taxonomy. The main gap is not naming or folder layout. The gap is runtime shape abstraction.

Recommended top-level abstraction:

```text
AgentRuntimeProfile
  LocalRuntimeProfile
  DistributedRuntimeProfile

RuntimeProfile owns:
  - session identity and affinity
  - workspace boundary
  - memory backend selection
  - event store selection
  - registry/discovery strategy
  - execution backend and sandbox policy
  - channel capabilities
```

This should stay above specific implementations. `QueryLoop` should consume ready collaborators, not branch on local vs distributed mode.

## P0 Iteration Themes

### P0.1 TencentDB Agent Memory Style Symbolic Context Map

Source-backed insight:

- TencentDB Agent Memory is not only a local file memory system.
- It combines short-term offload files with long-term hierarchical memory.
- The unique part is the high-density symbolic map, especially the MMD Mermaid-style state graph and node mapping.
- The LLM sees a compact symbolic map first, then progressively drills down into referenced evidence.

agent-os implication:

Add a memory/context layer that can project large historical/tool context as a compact symbolic navigation surface:

```text
Raw evidence
  -> offload/store
  -> symbolic context map
  -> LLM-visible compact projection
  -> recall/read evidence by handle
```

Suggested interfaces:

```python
class SymbolicContextMapBuilder(Protocol):
    def build(self, evidence: Sequence[ContextEvidence]) -> SymbolicContextMap: ...

class SymbolicContextMapStore(Protocol):
    def save(self, session_id: str, context_map: SymbolicContextMap) -> None: ...
    def load(self, session_id: str) -> SymbolicContextMap | None: ...

class EvidenceRecallStore(Protocol):
    def put(self, evidence: ContextEvidence) -> EvidenceHandle: ...
    def read(self, handle: EvidenceHandle, *, range: EvidenceRange | None = None) -> str: ...
```

Design rules:

- The symbolic map is a projection, not the source of truth.
- Evidence handles must be stable and drill-down capable.
- Default prompt should show the map only when it reduces context pressure.
- This may increase function calling because the model needs to inspect evidence by handle. That is acceptable when it avoids putting large raw context into every provider call.

Do not mix this with the current tool-result cap design. Tool-result cap handles oversized single tool outputs at ingestion. Symbolic maps handle cross-turn memory density and navigation.

### P0.2 Runtime Profile Abstraction For Local And Distributed Agents

The user requirement is explicit: agent-os should support local proxy agents and web distributed agents.

Recommended contracts:

```python
class RuntimeProfile(Protocol):
    session_provider: SessionProvider
    workspace_provider: WorkspaceProvider
    memory_provider: MemoryProvider
    event_store: RunEventStore
    execution_backend: ExecutionBackend
    registry: AgentRegistry | None
    channel_capabilities: ChannelCapabilities
```

Local profile:

- in-process session state
- local filesystem workspace
- local memory backends such as SQLite/filesystem/Qdrant local
- local event log
- direct tool execution with local permission policy
- no mandatory registry

Distributed profile:

- durable session provider
- worker registry/discovery
- remote workspace binding
- append-only run event store
- queue or task store for worker dispatch
- SSE/HTTP resume support
- permission context bound to workspace and identity

Implementation guidance:

- Prefer `Protocol` or ABC boundaries where agent-os already needs multiple implementations.
- Do not put local/distributed branching inside `QueryLoop`.
- Construct the profile in builder/factory code, then inject collaborators.

### P0.3 Durable Run Event Store And Replay

DeerFlow's strongest transferable idea is production runtime reliability:

- append-only run events
- per-thread/run event store
- checkpointer integration
- replay fixture keys that include caller identity
- tool output externalization

agent-os already has typed events and SSE resume specs. The next step should connect them:

```text
Typed runtime events
  -> RunEventStore append
  -> SSE reader consumes durable event stream
  -> Last-Event-ID can resume mid-turn
  -> replay tests can reconstruct run behavior
```

Suggested interface:

```python
class RunEventStore(Protocol):
    def append(self, event: RuntimeEvent) -> StoredRunEvent: ...
    def read_after(self, run_id: str, event_id: str | None) -> Iterable[StoredRunEvent]: ...
```

Design rules:

- EventBus remains observation-only.
- RunEventStore is persistence, not hook interception.
- SSE resume should read from the event store or a bounded hot buffer backed by the store.
- Replay keys should include caller or scenario identity, not only conversation text.

## P1 Iteration Themes

### P1.1 AgentScope-Inspired Workspace And Permission Boundary

AgentScope 2.x is strongest in distributed/Web agent runtime design:

- session, workspace, storage, message bus, and permission context are first-class.
- workspace root is part of the permission context.
- Docker workspace separates host workdir and container workdir.
- FastAPI app factory exposes runtime extension points.

agent-os implication:

Make workspace identity a hard runtime boundary:

```python
class WorkspaceProvider(Protocol):
    def resolve(self, session: SessionIdentity) -> WorkspaceRef: ...

class PermissionContextProvider(Protocol):
    def for_workspace(self, workspace: WorkspaceRef, identity: ActorIdentity) -> PermissionContext: ...
```

This should affect:

- tool execution
- file access
- sandbox backend selection
- remote worker dispatch
- memory and event path scoping

Avoid treating workspace as only a filesystem path.

### P1.2 AgentScope Java Style Registry And A2A Boundary

AgentScope Java is the better source for AgentCard/A2A/Nacos style service discovery. Python AgentScope 2.x is not the source-backed reference for that now.

agent-os implication:

Keep registry in two layers:

1. Lightweight internal worker registry for agent-os distributed mode.
2. Optional external AgentCard/A2A-compatible boundary for cross-system interoperability.

Do not force A2A/Nacos-style semantics into the internal worker registry. They solve different problems.

### P1.3 OpenHarness Product Shell Separation

OpenHarness is valuable because the runtime SDK and product agent shell are separate:

- SDK runtime owns engine, commands, memory scan, channel, tasks.
- Product layer owns persona, personal workspace, app-specific memory, channel policy.
- Auto-dream style memory consolidation uses backup/diff/lock guardrails.

agent-os implication:

Do not put a specific personal assistant behavior into SDK core. Provide extension points:

- product shell prompt/persona injection
- app-level workspace policy
- app-level memory extraction/consolidation
- app-level channel policy

### P1.4 MemPalace And SimpleMem Memory Backend Lessons

MemPalace:

- memory backends/source adapters deserve explicit plugin contracts.
- conformance tests are as important as the adapter API.
- hybrid retrieval and reranking should be backend-independent.

SimpleMem:

- AutoMemory router can hide text/omni backends, but backend switching after first use is risky.
- EvolveMem is more useful as an evaluation/optimizer idea than as core runtime behavior.

agent-os implication:

Add a memory backend conformance suite before expanding backend count:

```text
store -> retrieve -> update -> delete/expire -> serialize -> restore -> rank consistency
```

Do not add multiple memory backends without a shared behavioral test contract.

### P1.5 Hermes Provider And Memory Fencing Lessons

Hermes is useful for productized provider/channel adaptation:

- provider fallback chain
- model catalog cache
- gateway/TUI separation
- memory-context fencing and output scrubber

agent-os implication:

- Provider fallback should remain provider-layer policy, not query-loop branching.
- Memory context needs explicit boundaries in rendered context.
- Avoid leaking memory-context content into assistant output when it is meant only as hidden support.

## Recommended Implementation Order

1. Finish the existing context-budget hardening branch.
   - Tool-result cap is already scoped.
   - Then token-aware compression circuit breaker.
   - Then SSE resume backed by a real event stream.

2. Add `RunEventStore` and connect typed events to persistence.
   - Start with local JSONL or SQLite implementation.
   - Keep interface compatible with distributed storage later.

3. Introduce `RuntimeProfile`.
   - Implement `LocalRuntimeProfile` first using existing collaborators.
   - Implement `DistributedRuntimeProfile` as an integration shell around existing channel/multi/session modules.

4. Introduce `WorkspaceProvider` and permission-context binding.
   - Make tool execution consume workspace and permission context explicitly.
   - Prepare Docker/E2B style execution backends without implementing all of them in one diff.

5. Add symbolic context map as an optional memory/context projection.
   - First implement types and local store.
   - Then add context renderer projection.
   - Then add recall/read-by-handle tool surface.

6. Add registry split.
   - Internal worker registry first.
   - External AgentCard/A2A compatibility later.

## Suggested File Areas

Likely new or changed modules:

- `src/agentos/runtime/profile.py`
- `src/agentos/runtime/session.py`
- `src/agentos/persistence/run_events.py`
- `src/agentos/channels/sse_buffer.py`
- `src/agentos/channels/sse_turns.py`
- `src/agentos/policies/security.py`
- `src/agentos/workspace/`
- `src/agentos/memory/symbolic_map.py`
- `src/agentos/memory/evidence.py`
- `src/agentos/registry/`

Before adding new modules, check current code. Some responsibilities may already exist under `multi/`, `channels/`, `memory/`, or `attachments/`.

## Anti-Patterns To Avoid

- Do not turn `QueryLoop` into a local/distributed switchboard.
- Do not make memory recall a generic "dump full raw memory into prompt" operation.
- Do not introduce a registry abstraction that only fits A2A and cannot serve internal worker dispatch.
- Do not add a memory backend without conformance tests.
- Do not store full oversized tool results in active messages by default.
- Do not let EventBus handlers mutate execution flow.
- Do not treat workspace as a string path once distributed execution is in scope.

## Open Questions For The New Window

1. Should `RuntimeProfile` be public API or builder-internal at first?
2. Should symbolic context map live under `memory/` or `context/`?
   - Recommended: store/build under `memory/`, render projection through `context/`.
3. Should local `RunEventStore` be JSONL or SQLite?
   - Recommended: JSONL for first implementation if append/read-after is enough; SQLite if querying by session/run/tool is needed immediately.
4. Should the first distributed profile require Redis/Postgres, or stay interface-only?
   - Recommended: interface plus one minimal in-memory/local implementation first, then Redis/Postgres in separate diffs.

## Handoff Summary

The strongest new KB signal is:

```text
agent-os should become profile-driven.
Local proxy and Web distributed agent are two runtime profiles sharing one context-first cognition model.
TencentDB Agent Memory adds the missing memory direction: symbolic high-density context maps plus evidence drill-down.
DeerFlow adds the missing production reliability direction: durable run events plus replay/resume.
AgentScope adds the missing distributed runtime boundary: workspace/session/permission/message-bus separation.
```

This is enough to start the next agent-os iteration without re-reading all source projects in the new window.
