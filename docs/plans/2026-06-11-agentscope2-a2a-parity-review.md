# AgentScope2 And A2A Parity Review

> **SUPERSEDED FOR LOOP TOPOLOGY:** 本历史评审中的旧双 Loop 与 Agent API
> 描述已被 `docs/superpowers/specs/2026-07-12-agentos-single-async-query-loop-design.md`
> 取代；历史正文保留。
>
> Date: 2026-06-11  
> Branch: `review/agentos-sdk-architecture-20260611`  
> Status: architecture review note  
> Related roadmap: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Target Conclusion

```text
agent-os should evolve from a context-first runtime SDK into a profile-driven agent application SDK.
The next production line is:
workspace/session boundary -> protocol card/discovery -> team runtime -> planner/subagent templates.
```

Do not jump directly to team discussion or planner templates before workspace and protocol discovery are explicit. Otherwise workers, tools, A2A peers, and web sessions will inherit implicit process state.

## External Baseline

Reviewed public references on 2026-06-11:

- AgentScope 2.0 Agent Service: https://docs.agentscope.io/v2/deploy/agent-service
- AgentScope 2.0 Agent Team: https://docs.agentscope.io/v2/deploy/agent-team
- A2A Agent Discovery: https://a2a-protocol.org/latest/topics/agent-discovery/
- A2A Specification: https://a2a-protocol.org/latest/specification/

Baseline interpretation:

- AgentScope-style service hosting owns routing, sessions, shared storage, message bus, workspace lifecycle, scheduling, and credentials. HTTP wrapping alone is not a production agent service.
- AgentScope-style teams make leader and workers independent sessions. Communication flows through message bus/inbox/wakeup, not direct shared active messages.
- A2A interoperability requires Agent Cards, discovery, capabilities/skills metadata, security schemes, and task/message operation coverage. A JSON `/a2a/tasks` bridge is not full A2A.

## Current agent-os Capability Map

| Shape | Current support | Evidence | Production gap |
|-------|-----------------|----------|----------------|
| Terminal/script agent | Direct | `AgentBuilder`, `QueryLoop`, sync `Agent.run()` | None for base use |
| Async/web-hosted agent | Direct at loop level | `AsyncQueryLoop`, `Agent.async_run()` | Profile defaults and adapter policy still evolving |
| Single-process HTTP/SSE agent | Direct | `AsgiAgentApp`, `InMemoryAgentSessionProvider`, SSE buffer | Multi-node state and workspace policy |
| Web distributed session agent | Primitives ready | `DurableAgentSessionProvider`, `SessionLeaseStore`, `SessionPersistence` | Redis/Postgres lease/snapshot adapters and workspace binding |
| Registry/discovery agent | Primitives ready | `AgentCard`, `PersistentAgentRegistry`, `ServiceResolver`, `A2AAgentCard`, `A2ACardResolver`, well-known route | Full signed-card trust, auth enforcement, health automation, complete skills/interfaces mapping |
| Minimal remote task bridge | Primitives ready | `A2AAdapter`, `A2AServerAdapter`, `/a2a/tasks`, A2A Agent Card publication/discovery | Full A2A task/message/streaming/push/auth operation parity |
| Local multi-agent coordination | Direct | `AgentCoordinator`, `TaskTable`, `AgentInbox`, `SpawnExecutor` | Team semantics are not first-class |
| Distributed multi-agent tasks | Primitives ready | `PostgresTaskStore`, `RedisAgentMessageQueue`, `RedisContinuationTrigger` | Worker session lifecycle and distributed team store |
| Team discussion agent | Primitives ready | `TeamRuntime`, `TeamRecord`, `TeamMessage`, `TeamNoticeStore` | Automatic worker sessions, team tools, distributed team store, UI stream |
| Planner/intent-router agent | Primitives ready | `PlannerRuntime`, `PlannerTools`, `PlanState`, `SubAgentTemplate`, `PlanRetryPolicy`, `EvidenceHandle`, `PostgresPlanStore`, `plan_to_working_state_summary`, planner pattern examples | Automatic decomposition, DAG scheduling, worker dispatch loop, complex compensation policy |

## Recommended Phase Order

### Phase 3: Workspace And Permission Boundary

Target:

```text
Workspace is a profile/session/team execution boundary.
Terminal can default to cwd; web/team must be explicit.
```

Why first:

- AgentScope service/team both rely on workspace lifecycle.
- A2A cards must not leak local paths but should later advertise resource expectations.
- Planner/subagent execution needs artifact/evidence boundaries.

Phase 3A artifact:

- `docs/superpowers/specs/2026-06-11-workspace-permission-boundary-design.md`
- `docs/superpowers/plans/2026-06-11-workspace-permission-boundary-implementation-plan.md`

### Phase 4: A2A Protocol Card And Discovery

Target:

```text
Internal agent-os AgentCard remains a routing card.
A2A protocol card becomes a separate compatibility surface or adapter.
```

Recommended slice:

- Add `A2AAgentCard` model or adapter.
- Add golden serialization tests aligned with A2A required fields.
- Add `/.well-known/agent-card.json` route behind `AsgiAgentApp` when configured.
- Add resolver that can load direct/static/well-known cards.
- Keep `/a2a/tasks` labeled internal until task/message operation parity exists.

Acceptance evidence:

- Golden card JSON tests.
- Well-known route test.
- Resolver test for static and HTTP/well-known transport.
- Skill docs distinguish internal bridge vs A2A compliance.

### Phase 5: Team Runtime And Distributed Wakeup

Target:

```text
Leader and workers are independent sessions coordinated through team messages,
inboxes, wakeup notices, and artifact handles.
```

Recommended slice:

- Add `TeamRecord`, `TeamMemberRecord`, `TeamMessage`.
- Add team store protocol with in-memory implementation.
- Add `TeamRuntime` on top of `AgentMessageQueue` and continuation notices.
- Add first team tools: `team_create`, `agent_create`, `team_say`, `team_delete`.
- Worker sessions receive narrowed `WorkspaceHandle` values.

Acceptance evidence:

- Leader creates team and worker.
- Worker receives team message through queue.
- Worker result wakes leader through continuation path.
- Parent and worker do not share active messages or working state.

### Phase 6: Planner / Intent Router / Subagent Templates

Target:

```text
Planner is a composable SDK pattern, not a hard-coded QueryLoop branch.
```

Phase 6A implemented slice:

- Add `SubAgentTemplate` for role, tools, workspace policy, context seed.
- Add `PlanState`, `PlanStep`, assignment and evidence handle dataclasses.
- Add `PlannerRuntime` tests for template-backed spawn/dispatch and evidence collection.
- Keep planner state out of default runtime loop.

Current acceptance evidence:

- Planner runtime tests assign steps to template-backed subagents through the coordinator boundary.
- Plan-and-execute primitives can assign steps and collect evidence handles.
- Docs say planner is a pattern layer, not a loop replacement. Phase 6B adds owner-scoped planner tools, Phase 6C adds working-state summary projection plus intent-router / plan-and-execute examples, later phases add `PostgresPlanStore`, and Phase 39 adds auditable step failure/retry metadata through `PlanRetryPolicy`, `plan_fail_step`, `plan_retryable_steps`, and `plan_retry_step`; automatic decomposition, DAG scheduling, worker dispatch loops, and complex compensation remain app/profile work.

## Key Design Decisions

1. Keep `QueryLoop` unaware of profiles, workspace, web, Redis, Postgres, A2A, and team runtime.
2. Keep internal `AgentCard` small for routing. Add protocol adapter/model for A2A.
3. Treat Redis hot state, Postgres durable memory, and `SessionSnapshot` as distinct projections unless an adapter explicitly implements the target protocol.
4. Do not make async mandatory for terminal agents. Prefer async for web hosts and distributed I/O.
5. Do not claim production web readiness until durable session provider is wired to distributed lease/snapshot adapters and explicit workspace strategy.
6. A2A Agent Card publication/discovery primitives are now a separate compatibility layer; do not claim full A2A compliance until operation parity, streaming/push behavior, auth enforcement, and trust handling land.
7. Use `agentos.readiness` as the production readiness matrix before claiming any form is production-ready; capability labels alone are insufficient.

## Immediate Next Execution Slice

Execute Phase 3A workspace boundary plan before A2A/team work:

```text
Workspace primitives -> public API -> RuntimeProfile metadata -> docs -> verification.
```

This is the smallest slice that improves future AgentScope2 parity without overfitting to one protocol or team implementation.
