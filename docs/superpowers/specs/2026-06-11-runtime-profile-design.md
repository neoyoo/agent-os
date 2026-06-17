# RuntimeProfile 设计（Phase 1）

> Status: draft  
> Date: 2026-06-11  
> Parent roadmap: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`  
> Scope: 定义 agent-os 从 context-first runtime SDK 走向 profile-driven application SDK 的装配层边界。

## Scope Contract

本设计属于 Phase 1：RuntimeProfile Design。

目标结论：

```text
SDK 的部署形态必须通过 RuntimeProfile 表达；
QueryLoop 只消费 collaborators，不知道 local/web/distributed。
```

本设计完成：

- 定义 `RuntimeProfile` 的职责边界。
- 定义首批 deployment shape：`LocalRuntimeProfile`、`WebRuntimeProfile`、`DistributedAgentProfile`。
- 定义 profile 与 `AgentBuilder`、`AsgiAgentApp`、`AgentSessionProvider`、persistence、multi-agent、registry 的关系。
- 明确哪些已有设计继续有效：`PersistentAgentSessionProvider`、SSE resume、distributed multi-agent task primitives、A2A task bridge。
- 给出最小实现顺序和验收标准。

本设计不完成：

- 不实现 profile 代码。
- 不实现生产 `PersistentAgentSessionProvider`；它已有独立设计：`docs/superpowers/specs/2026-05-28-persistent-session-provider-design.md`。
- 不实现 full A2A protocol。
- 不实现 team discussion runtime。
- 不实现 workspace sandbox。
- 不改变 `QueryLoop` 的 turn orchestration。

## Current Evidence

当前代码已经有这些 primitives：

| Area | Evidence | Readiness |
|------|----------|-----------|
| Sync loop | `AgentBuilder.build()`, `QueryLoop` | Direct |
| Async loop | `AgentBuilder.build_async()`, `AsyncQueryLoop` | Direct |
| ASGI channel | `AsgiAgentApp`, JSON turn, SSE turn, health/ready, auth/rate-limit hooks | Direct for single process |
| Session snapshot | `SessionSnapshot`, `SessionPersistence`, serializers, SQLite/FileSystem/Memory | Direct primitives |
| Redis/Postgres projections | `RedisHotSessionStore`, `PostgresDurableSessionStore` | Memory/session projection primitives |
| Persistent web session provider | Separate draft spec only | Not implemented |
| SSE resume | `SseEventBuffer`, in-memory implementation, ASGI turn background runner | Direct single-node; distributed buffer depends on adapter |
| Distributed task store | `PostgresTaskStore`, `TaskStore` contract | Direct primitives |
| Distributed message queue | `RedisAgentMessageQueue`, `AgentMessageQueue` contract | Direct primitives |
| Registry/discovery | `AgentCard`, `PersistentAgentRegistry`, `ServiceResolver`, `PostgresAgentRegistryStore` | Internal registry primitives |
| A2A task bridge | `A2AAdapter`, `A2AServerAdapter`, `/a2a/tasks` | Internal task bridge |

The gap is not "missing every backend"; the gap is the assembly contract that chooses the right collaborators for a deployment shape and prevents shape-specific branching from leaking into `QueryLoop`.

## Design Principles

1. `RuntimeProfile` is assembly, not orchestration.
2. `QueryLoop` remains responsible only for turn execution.
3. Channels own request/response protocol, not cognition state.
4. Session providers own hydrate/save lifecycle.
5. Workspace and permissions are profile-level execution boundaries, not random tool parameters.
6. Registry/discovery is optional for local profiles and required for distributed profiles.
7. A2A compatibility is a protocol adapter layer, not the internal worker registry itself.

## Public API Decision

Recommendation:

```text
Make RuntimeProfile public, but mark initial implementations as conservative and small.
```

Reason:

- Users need a stable way to choose terminal vs web distributed form.
- Keeping it builder-internal would hide the exact production boundary we need people to reason about.
- The first API can be intentionally narrow: profile assembles collaborators and channel wrappers; it does not expose every future policy.

Initial public import target:

```python
from agentos.runtime.profile import (
    RuntimeProfile,
    LocalRuntimeProfile,
    WebRuntimeProfile,
    DistributedAgentProfile,
)
```

`AgentBuilder.profile(profile)` may be added after the profile module exists, but it should not be required in the first implementation if the profile can expose explicit build helpers.

## Core Contracts

### RuntimeProfile

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.runtime import Agent


class RuntimeProfile(Protocol):
    """Deployment-shape assembly boundary.

    A profile creates or exposes ready collaborators for a deployment shape.
    It does not run a turn and does not mutate QueryLoop behavior.
    """

    name: str

    def build_agent(self, session_id: str | None = None) -> Agent:
        """Build an Agent for local or per-session use."""
```

This minimal protocol is intentionally small. More capabilities are added by optional profile interfaces instead of forcing every profile to implement every deployment concern.

### ChannelRuntimeProfile

```python
class ChannelRuntimeProfile(RuntimeProfile, Protocol):
    """Profile that can expose an inbound channel application."""

    def build_channel_app(self) -> object:
        """Build ASGI or other channel app."""
```

### DistributedRuntimeProfile

```python
class DistributedRuntimeProfile(ChannelRuntimeProfile, Protocol):
    """Profile with registry, durable session, and distributed task boundaries."""

    def readiness_checks(self) -> dict[str, object]:
        """Return backend readiness checks for /ready integration."""
```

## Profile Implementations

### LocalRuntimeProfile

Goal:

```text
Make terminal/script/local service usage explicit and stable.
```

Responsibilities:

- use `AgentBuilder.build()` by default
- optionally use `AgentBuilder.build_async()` if requested
- local in-process session state
- local filesystem workspace default
- optional SQLite/FileSystem/Memory `SessionPersistence`
- no mandatory registry
- no distributed lock

Non-goals:

- no ASGI app by default
- no Redis/Postgres requirement
- no session affinity

Suggested constructor:

```python
@dataclass(slots=True)
class LocalRuntimeProfile:
    agent_builder: AgentBuilder
    loop_mode: Literal["sync", "async"] = "sync"
    session_persistence: SessionPersistence | None = None

    name: str = "local"

    def build_agent(self, session_id: str | None = None) -> Agent:
        return (
            self.agent_builder.build_async()
            if self.loop_mode == "async"
            else self.agent_builder.build()
        )
```

### WebRuntimeProfile

Goal:

```text
Make web service deployment explicit, with clear distinction between single-process and production distributed session modes.
```

Responsibilities:

- choose `AsyncQueryLoop` by default
- build `AsgiAgentApp`
- require an `AgentSessionProvider`
- wire auth/rate-limit/readiness/shutdown options
- expose whether session lifecycle is in-memory or durable
- integrate future `PersistentAgentSessionProvider` without changing `QueryLoop`

Suggested constructor:

```python
@dataclass(slots=True)
class WebRuntimeProfile:
    session_provider: AgentSessionProvider
    auth_policy: ChannelAuthPolicy | None = None
    rate_limiter: RateLimiter | None = None
    readiness_checks: Mapping[str, Callable[[], object]] | None = None
    health_checks: Mapping[str, Callable[[], object]] | None = None
    a2a_server: A2AServerAdapter | None = None

    name: str = "web"

    def build_agent(self, session_id: str | None = None) -> Agent:
        if session_id is None:
            raise ValueError("WebRuntimeProfile requires a session_id")
        return self.session_provider.get_agent(session_id)

    def build_channel_app(self) -> AsgiAgentApp:
        return AsgiAgentApp(
            sessions=self.session_provider,
            auth_policy=self.auth_policy,
            rate_limiter=self.rate_limiter,
            readiness_checks=self.readiness_checks,
            health_checks=self.health_checks,
            a2a_server=self.a2a_server,
        )
```

Important boundary:

- `WebRuntimeProfile` may use `InMemoryAgentSessionProvider` for single-process service.
- Production arbitrary-node routing requires future `PersistentAgentSessionProvider` or equivalent app-owned provider.
- The profile must make that readiness visible in docs and tests.

### DistributedAgentProfile

Goal:

```text
Assemble distributed task coordination without pretending it is team discussion or full A2A.
```

Responsibilities:

- own `AgentCoordinator` assembly
- inject `TaskStore`
- inject `AgentMessageQueue`
- inject `AgentRegistry` or `AgentResolver`
- optionally inject `RemoteTaskExecutor`
- expose worker readiness checks
- keep internal registry separate from external A2A card compatibility

Suggested constructor:

```python
@dataclass(slots=True)
class DistributedAgentProfile:
    coordinator: AgentCoordinator
    resolver: AgentResolver | None = None
    registry: PersistentAgentRegistry | None = None
    remote_task_executor: RemoteTaskExecutor | None = None

    name: str = "distributed-agent"

    def build_agent(self, session_id: str | None = None) -> Agent:
        raise NotImplementedError(
            "DistributedAgentProfile assembles coordination, not a single agent",
        )
```

This profile is not a team discussion runtime. It is the assembly profile for existing task primitives. Team discussion belongs to roadmap Phase 5.

## Workspace Boundary

`RuntimeProfile` should reserve workspace hooks even if Phase 1 does not implement the full workspace module.

Suggested future protocols:

```python
@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    workspace_id: str
    root: str | None = None
    metadata: dict[str, object] | None = None


class WorkspaceProvider(Protocol):
    def resolve(self, *, session_id: str, actor_id: str | None = None) -> WorkspaceHandle:
        """Return the workspace boundary for this session/actor."""
```

Phase 1 should not retrofit every tool. It should document that future tool execution must receive workspace context from profile/session assembly, not from `QueryLoop`.

## Relationship To Existing Specs

| Existing spec | Relationship |
|---------------|--------------|
| `2026-05-28-persistent-session-provider-design.md` | Implements the durable session provider that `WebRuntimeProfile` should inject. |
| `2026-05-28-sse-resume-mid-turn-design.md` | Channel behavior under `WebRuntimeProfile`; profile wires buffer/readiness, channel owns event replay. |
| `2026-05-28-cross-machine-a2a-dispatch-design.md` | Improves internal remote task bridge; full A2A compliance is still later. |
| `docs/todo-distributed-multi-agent-messaging.md` | Supplies distributed task/message primitives for `DistributedAgentProfile`. |
| `2026-05-28-execution-backend-sandbox-seam-design.md` | Future workspace/execution backend source for profile policy. |

## File Map For First Implementation Plan

This is not an implementation plan, but the likely file boundaries are:

| File | Responsibility |
|------|----------------|
| `src/agentos/runtime/profile.py` | Protocols and initial profile dataclasses |
| `src/agentos/runtime/__init__.py` | Export public profile names |
| `src/agentos/__init__.py` | Top-level exports after public API decision |
| `src/agentos/builder.py` | Optional `.profile(...)` hook or helper only after minimal profile tests pass |
| `tests/runtime/test_runtime_profile.py` | Local/Web profile assembly tests |
| `.claude/skills/agent-os/modules/architecture.md` | Keep docs aligned with profile API |

Do not modify `src/agentos/runtime/query_loop.py` for profile work unless a test proves a missing collaborator seam. The default expectation is zero `QueryLoop` changes.

## Acceptance Criteria

### AC-1: Local profile builds equivalent agents

```text
Given an AgentBuilder with FakeProvider,
LocalRuntimeProfile(loop_mode="sync").build_agent() returns Agent using QueryLoop.
LocalRuntimeProfile(loop_mode="async").build_agent() returns Agent using AsyncQueryLoop.
Existing AgentBuilder.build/build_async behavior remains unchanged.
```

### AC-2: Web profile builds ASGI app without owning turn logic

```text
Given InMemoryAgentSessionProvider,
WebRuntimeProfile.build_channel_app() returns AsgiAgentApp.
POST /v1/sessions/{id}/turns still uses AsgiAgentApp + AgentSessionProvider.
No profile code calls provider.complete(), ToolCallRouter, or QueryLoop.run_turn directly.
```

### AC-3: Durable web profile readiness is explicit

```text
If session_provider is in-memory, profile readiness metadata says single-process.
If session_provider is persistent/durable, profile readiness metadata says durable-session.
Docs must not describe in-memory provider as multi-node-ready.
```

### AC-4: Distributed profile is task coordination only

```text
DistributedAgentProfile can hold AgentCoordinator, TaskStore, AgentMessageQueue, registry/resolver.
It must not claim team discussion support or full A2A support.
```

### AC-5: QueryLoop remains deployment-agnostic

```text
No import from agentos.runtime.query_loop.py to agentos.channels, agentos.registry, Redis, Postgres, or profile module.
Existing architecture test should continue to prove runtime/context/messages do not import channels.
```

## Open Decisions Before Code

1. Should `RuntimeProfile` live under `agentos.runtime.profile` or top-level `agentos.profile`?
   - Recommendation: `agentos.runtime.profile`, because it assembles runtime collaborators and should stay close to `Agent` / `QueryLoop`.
2. Should `AgentBuilder.profile(profile)` exist in first implementation?
   - Recommendation: defer unless a test needs it. Start with profile classes wrapping builder/session/channel assembly.
3. Should `WebRuntimeProfile` accept `AgentBuilder` directly and build its own session provider?
   - Recommendation: no. Accept a session provider. Let `PersistentAgentSessionProvider` own hydrate/save.
4. Should profile include `RunEventStore` now?
   - Recommendation: reserve field later. Do not block Phase 1 on event-store implementation.

## Non-Goals And Guardrails

- Do not add local/distributed conditionals inside `QueryLoop`.
- Do not turn `AgentBuilder` into a large service container.
- Do not claim Redis/Postgres primitives make web distributed sessions complete without a durable session provider and lock policy.
- Do not couple internal `AgentCard` directly to external A2A Agent Card semantics.
- Do not make workspace a plain string path in public profile API if distributed execution is in scope.
- Do not add a profile abstraction that only wraps one existing class and provides no deployment-shape clarity.

## Phase 1 Completion Checklist

Before Phase 1 implementation can be called complete, report:

| Requirement | Evidence |
|-------------|----------|
| `RuntimeProfile` public contract exists | source file + public API tests |
| Local profile sync/async builds agents | targeted tests |
| Web profile builds ASGI app from session provider | targeted tests |
| Profile docs distinguish single-process vs multi-node | docs diff |
| QueryLoop unchanged or still deployment-agnostic | import-boundary test |
| Existing suite passes | full `uv run pytest -q`, compileall, diff check |

