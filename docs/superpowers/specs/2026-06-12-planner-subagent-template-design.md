# Planner And SubAgent Template Design (Phase 6A)

> Date: 2026-06-12  
> Branch: `review/agentos-sdk-architecture-20260611`  
> Status: Phase 6A implemented  
> Related roadmap: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Target Conclusion

```text
Planner, intent-router, and plan-and-execute are composable SDK patterns.
They should be expressed through plan state, subagent templates, assignment
records, and evidence handles, not as a hard-coded QueryLoop branch.
```

Phase 6A adds stable primitives that make common planner patterns easy to build
while preserving the core runtime boundary. `QueryLoop` still executes one agent
turn. Planning is app/tool/runtime composition around that loop.

## External Baseline

Reviewed on 2026-06-12:

- AgentScope Agent Service documentation describes task planning, agent teams,
  workspace, message bus, and scheduling as production service responsibilities:
  https://docs.agentscope.io/v2/deploy/agent-service
- AgentScope Agent Team documentation models leaders and workers as independent
  sessions coordinated by team tools and a message bus:
  https://docs.agentscope.io/v2/deploy/agent-team

Baseline interpretation:

- Production multi-agent planning needs explicit plan state and artifact handles.
  Passing unstructured chat transcripts between agents is not enough.
- Subagents should be instantiated from templates: role, capabilities, context
  seed, tool policy, workspace policy, and optional target agent id.
- Planner state should be recoverable and serializable. It should not live only
  inside a prompt or hidden Python closure.

## agent-os Fit

Existing primitives that should be reused:

- `AgentCoordinator` can spawn isolated subagents or dispatch to persistent
  experts.
- `TeamRuntime` can store cross-agent conversation messages without sharing
  active messages.
- `WorkspaceHandle` and `WorkspacePolicy` can express narrowed execution
  boundaries for subagents.
- `TaskRequest`, `TaskResult`, and artifact dictionaries can carry result data.
- `ContextState.working_state` can project current plan summaries into prompts,
  but should not become the only source of truth for planner state.

Pre-Phase 6A gaps addressed by this slice:

- No `SubAgentTemplate` to define reusable subagent roles, capabilities, tool
  allowlists, context seed, and workspace scope.
- No `PlanState`, `PlanStep`, `PlanAssignment`, or `EvidenceHandle`.
- No planner store protocol for serializable plan state.
- No planner tool wrapper that turns LLM tool calls into structured plan updates
  and coordinator dispatches. This remains Phase 6B+ work.
- Skill docs described planner as app-owned pattern only, without naming the
  primitives that should exist next. Phase 6A updates the skill docs to
  primitives-ready.

## Recommended Shape

Add `src/agentos/multi/planner.py` with:

- `PlanStatus = Literal["draft", "running", "completed", "failed", "cancelled"]`
- `PlanStepStatus = Literal["pending", "assigned", "running", "completed", "failed", "blocked", "cancelled"]`
- `EvidenceKind = Literal["text", "artifact", "task_result", "team_message", "external"]`
- `SubAgentTemplate`
- `EvidenceHandle`
- `PlanStep`
- `PlanAssignment`
- `PlanState`
- `PlanStore` protocol
- `InMemoryPlanStore`
- `PlannerRuntime`

Suggested dataclass shape:

```python
@dataclass(frozen=True, slots=True)
class SubAgentTemplate:
    template_id: str
    name: str
    role: str
    capabilities: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ()
    context_seed: tuple[str, ...] = ()
    workspace_scope: WorkspaceScope = "task"
    target_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceHandle:
    evidence_id: str
    kind: EvidenceKind
    summary: str
    uri: str | None = None
    producer_agent_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    instruction: str
    status: PlanStepStatus = "pending"
    required_capabilities: tuple[str, ...] = ()
    assigned_agent_id: str | None = None
    template_id: str | None = None
    task_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PlanState:
    plan_id: str
    objective: str
    owner_agent_id: str
    status: PlanStatus = "draft"
    steps: tuple[PlanStep, ...] = ()
    evidence: tuple[EvidenceHandle, ...] = ()
    created_at: float = 0
    updated_at: float = 0
    workspace: WorkspaceHandle | None = None
```

## Runtime Behavior

`PlannerRuntime.create_plan(...)`:

1. Creates a `PlanState(status="draft")`.
2. Stores it in `PlanStore`.
3. Does not mutate `QueryLoop` or agent active messages.

`PlannerRuntime.add_step(...)`:

1. Adds a pending `PlanStep`.
2. Records required capabilities and optional template id.
3. Leaves scheduling decisions explicit.

`PlannerRuntime.assign_step(...)`:

1. Resolves a `SubAgentTemplate`.
2. Chooses either `AgentCoordinator.spawn()` or `AgentCoordinator.dispatch()`:
   - `target_agent_id is None` -> spawn isolated subagent.
   - `target_agent_id is not None` -> dispatch by required capabilities or target.
3. Passes explicit `target_agent_id` through the coordinator dispatch boundary
   when a template binds to a persistent expert.
4. Writes `assigned` step state with `task_id`, `template_id`, and assigned agent
   information.
5. Passes allowed tools and timeout through existing task request paths.

`PlannerRuntime.record_evidence(...)`:

1. Stores an `EvidenceHandle`.
2. Attaches the evidence id to one or more plan steps.
3. Keeps artifact payloads external; the plan stores stable handles and summaries.

`PlannerRuntime.complete_step(...)`:

1. Marks a step completed or failed.
2. Optionally attaches evidence handles.
3. Completes the plan only when all steps are terminal and no failed/blocked step
   remains.

## Tool Layer Preview

Phase 6B adds `PlannerTools` that register:

- `plan_create`
- `plan_add_step`
- `plan_assign_step`
- `plan_record_evidence`
- `plan_status`

These tools should wrap `PlannerRuntime`; they should not bypass the store or
directly mutate context state. Apps can project plan summaries into
`ContextState.working_state` if desired.

## Non-Goals For Phase 6A

- No DAG engine or graph scheduler.
- No hard-coded planner loop inside `QueryLoop` or `AsyncQueryLoop`.
- No automatic decomposition by LLM.
- No mandatory team runtime dependency.
- No persistent database-backed plan store.
- No broad refactor of `AgentCoordinator`; Phase 6A only adds the narrow
  optional `target_agent_id` dispatch constraint needed by templates.

## Acceptance Criteria

- `SubAgentTemplate` can define role, capabilities, allowed tools, context seed,
  workspace scope, and optional target agent id.
- `PlanState` can store steps, assignments, evidence handles, and workspace.
- `InMemoryPlanStore` can create/update/read plans deterministically.
- `PlannerRuntime` can add steps, assign them to coordinator spawn/dispatch, and
  record evidence handles without sharing active messages.
- Template dispatch to a persistent expert preserves `target_agent_id` through
  the real `AgentCoordinator`, not only through fake coordinator tests.
- Public exports are available from `agentos.multi` and top-level `agentos`.
- Skill docs describe planner/intent-router as primitives-ready after Phase 6A,
  while automatic LLM decomposition, graph scheduling, and production plan store
  remain future work.
- `QueryLoop` and `AsyncQueryLoop` have no imports or references to planner,
  team runtime, queues, Redis, Postgres, A2A, or workspace.

## Phase 6B Follow-Up

After Phase 6A, Phase 6B adds `PlannerTools` and prepares examples:

- Intent-router example chooses a template and dispatches a single task.
- Plan-and-execute example creates multiple steps, assigns them, and records
  evidence handles from task results.
- Docs show how to project plan summaries into working state without making
  working state the source of truth.
