# Workspace And Permission Boundary Design (Phase 3)

> Status: draft  
> Date: 2026-06-11  
> Parent roadmap: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`  
> Previous phase: `docs/superpowers/specs/2026-06-11-production-web-session-hydration-design.md`

## Scope Contract

This design belongs to Phase 3: workspace and permission boundary.

Target conclusion:

```text
Workspace is an execution boundary for a session/profile/team, not an implementation detail of individual tools.
Terminal agents may default to the local cwd, but web and distributed agents must receive an explicit workspace handle.
Subagents and team workers may only receive the same or narrower workspace and permissions than their parent.
```

This phase completes:

- Define first-class workspace identifiers and handles.
- Define workspace strategy for terminal, web, and future team workers.
- Bind workspace access to existing security/resource policy concepts.
- Add public SDK primitives that later tools, durable session providers, and team runtimes can consume.
- Keep `QueryLoop` deployment-agnostic.

This phase does not complete:

- OS sandboxing, container execution, or remote filesystem mounts.
- Full tool rewrite to execute every side effect through a workspace-aware backend.
- Team discussion runtime.
- A2A Agent Card discovery.
- Redis/Postgres snapshot adapters.

Those are later phases. This phase creates the boundary and policy vocabulary first.

## External Baseline

AgentScope 2.0 Agent Service treats production hosting as a service layer that owns session state, request routing, storage, message bus, scheduling, and workspace lifecycle. Its distributed examples use shared storage/message bus so arbitrary workers can serve one logical service.

AgentScope 2.0 Agent Team models the leader and workers as independent sessions coordinated through a message bus and wakeup path. That means workspace cannot be an implicit process-wide current directory; workers need explicit resource boundaries.

A2A's current protocol surface uses Agent Cards, discovery, skills/capabilities metadata, security schemes, and task/message operations. Workspace policy is not a replacement for A2A metadata, but A2A cards later need to advertise resource and auth expectations without leaking local paths.

Reference sources used on 2026-06-11:

- https://docs.agentscope.io/v2/deploy/agent-service
- https://docs.agentscope.io/v2/deploy/agent-team
- https://a2a-protocol.org/latest/topics/agent-discovery/
- https://a2a-protocol.org/latest/specification/

## Current Evidence

Current agent-os has useful but incomplete primitives:

| Area | Evidence | Readiness |
|------|----------|-----------|
| Security policy | `src/agentos/policies/security.py` | Tool allow/deny only |
| Resource policy | `src/agentos/policies/resource_policy.py` | Runtime limits only |
| Tool execution | `src/agentos/capabilities/executor.py` | No workspace input |
| Runtime profile | `src/agentos/runtime/profile.py` | Can carry profile metadata, no workspace |
| Durable web session | `DurableAgentSessionProvider` | Session lifecycle boundary, no workspace |
| Multi-agent isolation | `SubagentInitRequest(context_strategy="isolated")` | Context isolation only |

Important current behavior:

- `ToolExecutor` validates security policy and resource policy but does not receive an execution root.
- `AgentCoordinator` spawns isolated subagents, but `SubagentInitRequest` does not include workspace policy.
- `WebRuntimeProfile` assembles an ASGI app from an injected session provider; it does not bind workspace metadata.
- No core runtime loop imports channel, persistence adapter, Redis, Postgres, or workspace concepts. That invariant should remain true.

## Design Principles

1. Workspace identity belongs above `QueryLoop`.
2. A workspace handle is a capability boundary, not just a filesystem path string.
3. Terminal mode can default to cwd for ergonomics.
4. Web/distributed mode must require explicit workspace strategy.
5. Subagents cannot broaden parent permissions.
6. Workspace policy should compose with `SecurityPolicy` and `ResourcePolicy`, not replace them.
7. A future team runtime should pass workspace handles by reference and share artifacts explicitly.
8. The first implementation should be pure Python primitives and tests; no OS sandbox claims.

## Core Contracts

### WorkspaceScope

```python
WorkspaceScope = Literal["process", "agent", "user", "session", "team", "task"]
```

Meaning:

| Scope | Meaning |
|-------|---------|
| `process` | Local terminal/dev workspace tied to the current process cwd |
| `agent` | One logical agent identity owns the workspace |
| `user` | All sessions for one user share a workspace |
| `session` | One conversation/session owns the workspace |
| `team` | A leader and workers share an explicit team workspace |
| `task` | A delegated task gets a narrowed temporary workspace |

### WorkspaceHandle

```python
@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    workspace_id: str
    scope: WorkspaceScope
    root: str | None = None
    parent_workspace_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
```

Rules:

- `workspace_id` is stable and safe to persist.
- `root` is optional because remote/sandboxed workspaces may not expose a local path.
- `parent_workspace_id` records narrowing lineage for subagents/tasks.
- `metadata` is string-only to avoid accidentally persisting secrets or arbitrary objects.

### WorkspaceRequest

```python
@dataclass(frozen=True, slots=True)
class WorkspaceRequest:
    agent_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    team_id: str | None = None
    task_id: str | None = None
    requested_scope: WorkspaceScope = "session"
```

This keeps provider lookup explicit without forcing every profile to have all identities.

### WorkspaceProvider

```python
class WorkspaceProvider(Protocol):
    def resolve_workspace(self, request: WorkspaceRequest) -> WorkspaceHandle:
        """Return the workspace handle for a profile/session/task."""

    def narrow_workspace(
        self,
        parent: WorkspaceHandle,
        *,
        child_id: str,
        scope: WorkspaceScope = "task",
    ) -> WorkspaceHandle:
        """Return a child workspace that cannot broaden parent access."""
```

### LocalWorkspaceProvider

The first SDK implementation should include `LocalWorkspaceProvider`:

- Defaults to `Path.cwd()` only for `scope="process"`.
- Can derive stable IDs from agent/user/session/team/task fields.
- Uses an optional base directory for deterministic tests and web profile configuration.
- Does not create directories unless `create=True` is configured.

### WorkspacePolicy

```python
@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    allow_parent_access: bool = False
    allowed_scopes: frozenset[WorkspaceScope] = frozenset({"session", "task"})
    require_explicit_web_workspace: bool = True
```

The policy checks:

- Requested scope is allowed.
- Web/distributed profiles do not silently fall back to process cwd.
- Child workspace scope is not broader than parent.

## RuntimeProfile Integration

`RuntimeProfile` should expose workspace metadata without moving turn execution into the profile.

Minimal Phase 3 changes:

- `LocalRuntimeProfile` accepts optional `workspace_provider` and `workspace_request`; defaults to process cwd when omitted.
- `WebRuntimeProfile` accepts optional `workspace_provider` and requires a resolvable `WorkspaceHandle` when `session_lifecycle != "single-process"` or when explicitly configured for production.
- `DistributedAgentProfile` records workspace support as readiness metadata only; team runtime integration is deferred.

`QueryLoop` must not import `agentos.workspace`, `agentos.channels`, Redis, Postgres, or runtime profiles.

## Multi-Agent Integration

Extend `SubagentInitRequest` later with:

```python
workspace: WorkspaceHandle | None = None
workspace_policy: WorkspacePolicy | None = None
```

Phase 3 may add this field if tests are small and backward compatible. If not, the implementation plan can defer coordinator wiring and only introduce primitives plus docs.

Rules for later team/planner phases:

- `spawn` creates a task-scoped workspace by default.
- `dispatch` to persistent experts sends workspace references only when explicitly allowed.
- Workers must not receive broader tool allowlists, resource budgets, or workspace access than parents.
- Shared artifacts should be passed as artifact/evidence handles, not by sharing active message state.

## A2A/Registry Implications

Phase 4 A2A Agent Cards should not publish local workspace roots.

They may later publish high-level resource declarations such as:

- supported workspace scopes
- required auth schemes
- artifact exchange modes
- maximum attachment size
- streaming support

Current internal `AgentCard` is still not an A2A-compliant card. Phase 4 should add an adapter or protocol card model instead of bloating the internal routing card.

## Acceptance Criteria

### AC-3-1: Workspace primitives are public

```text
WorkspaceHandle, WorkspaceRequest, WorkspaceProvider, WorkspaceScope,
WorkspacePolicy, and LocalWorkspaceProvider are importable from agentos.workspace
and mirrored at top-level agentos if consistent with existing public API style.
```

### AC-3-2: Terminal default is ergonomic

```text
LocalWorkspaceProvider can resolve a process workspace rooted at cwd or an injected base path.
No existing AgentBuilder or LocalRuntimeProfile call breaks when no workspace is supplied.
```

### AC-3-3: Web production requires explicit workspace

```text
WebRuntimeProfile exposes enough metadata to distinguish explicit workspace configuration
from process-cwd fallback. Docs must warn that production web profiles need explicit strategy.
```

### AC-3-4: Subagent narrowing is enforceable

```text
WorkspacePolicy rejects child workspace requests that broaden parent scope.
Tests prove a parent session workspace can narrow to task scope, but a task workspace cannot widen to session/team/user/process.
```

### AC-3-5: QueryLoop remains deployment-agnostic

```text
No imports from query_loop.py or async_query_loop.py to workspace, channels,
persistence adapters, Redis, Postgres, or runtime.profile.
```

### AC-3-6: Docs and skill guidance are truthful

```text
agent-os skill docs state that workspace primitives exist,
but sandboxing/container enforcement and team workspace runtime remain future work.
```

## Implementation Slice

Phase 3A should implement:

- New `src/agentos/workspace.py` or `src/agentos/workspace/` package with primitives.
- Public exports.
- Tests for local provider resolution and policy narrowing.
- RuntimeProfile metadata only if existing profile code can accept it without broad refactor.
- Skill docs updates.

Phase 3A should not implement:

- Tool backend path rewriting.
- Remote workspace service.
- Container sandbox.
- A2A card workspace declarations.
- Team tools.

## Later Work

Phase 3B:

- Wire `WorkspaceHandle` into `ToolExecutor`/`ExecutionBackend`.
- Add workspace-aware file/shell tool helpers.
- Bind durable session hydration to workspace lookup.

Phase 4:

- Add A2A protocol card model, well-known discovery route, and resolver.
- Add card metadata for supported workspace scopes without exposing local paths.

Phase 5:

- Add team/member/session runtime.
- Use workspace narrowing for worker sessions and task artifacts.

## Completion Checklist

| Requirement | Evidence |
|-------------|----------|
| Public workspace primitives | source + public API tests |
| Local terminal default | provider tests |
| Web explicit strategy docs | skill docs diff |
| Subagent narrowing policy | policy tests |
| QueryLoop agnostic | import-boundary test/search |
| Existing suite passes | targeted tests, compileall, full pytest, diff check |
