# Planner And SubAgent Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 6A planner primitives so applications can model intent-router and plan-and-execute workflows with subagent templates, plan state, assignments, and evidence handles.

**Architecture:** Add `agentos.multi.planner` as a pattern layer on top of existing multi-agent primitives. `PlanStore` is the truth source for plan state; `PlannerRuntime` updates plans and delegates execution through an injected coordinator boundary. `QueryLoop` and `AsyncQueryLoop` remain unaware of planner concepts.

**Tech Stack:** Python 3.11 dataclasses/protocols, existing `AgentCoordinator` task APIs, `WorkspaceHandle`/`WorkspaceScope`, pytest.

**Implementation note:** During execution, template-backed persistent expert dispatch proved a narrow coordinator gap. Phase 6A therefore also adds optional `AgentCoordinator.dispatch(target_agent_id=...)` support so `SubAgentTemplate.target_agent_id` is preserved by the real coordinator, not only fake test doubles.

---

## Scope Contract

This plan implements only Phase 6A from `docs/superpowers/specs/2026-06-12-planner-subagent-template-design.md`.

Target conclusion:

```text
Planner, intent-router, and plan-and-execute are composable SDK patterns.
They are expressed through plan state, subagent templates, assignment records,
and evidence handles, not as a hard-coded QueryLoop branch.
```

Deferred:

- `PlannerTools` LLM-facing tools. Deferred from Phase 6A and implemented by
  the separate Phase 6B planner tools slice.
- Automatic LLM decomposition.
- DAG/graph scheduling.
- Persistent database-backed plan store.
- Production scheduling/retry policy.
- UI plan stream.

## File Structure

Create:

- `src/agentos/multi/planner.py`  
  Planner dataclasses, store protocol, in-memory store, coordinator protocol, and runtime.

- `tests/multi/test_planner_runtime.py`  
  Plan model/store tests, runtime add/assign/evidence behavior, and no QueryLoop coupling regression.

Modify:

- `src/agentos/multi/__init__.py`  
  Export planner public names.

- `src/agentos/__init__.py`  
  Mirror stable planner public names at top level.

- `tests/architecture/test_public_api.py`  
  Add public API assertions.

- `.claude/skills/agent-os/modules/agent-forms.md`  
  Update planner/intent-router readiness after Phase 6A.

- `.claude/skills/agent-os/modules/multi-agent.md`  
  Add planner primitives section and remaining gaps.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/context/state.py`
- `src/agentos/multi/coordinator.py` unless a test proves a narrow adapter gap. The executed slice did prove one gap: explicit `target_agent_id` dispatch for persistent expert templates.

## Task 1: Add Planner Models And In-Memory Store

**Files:**
- Create: `src/agentos/multi/planner.py`
- Create: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Write failing model/store tests**

Create `tests/multi/test_planner_runtime.py`:

```python
from __future__ import annotations

from agentos.multi.planner import (
    EvidenceHandle,
    InMemoryPlanStore,
    PlanState,
    PlanStep,
    SubAgentTemplate,
)


def test_subagent_template_captures_execution_policy() -> None:
    template = SubAgentTemplate(
        template_id="researcher",
        name="Researcher",
        role="Find and summarize evidence.",
        capabilities=("research", "summarize"),
        allowed_tool_names=("web_search",),
        context_seed=("Prefer primary sources.",),
        workspace_scope="task",
        target_agent_id="expert_researcher",
    )

    assert template.capabilities == ("research", "summarize")
    assert template.allowed_tool_names == ("web_search",)
    assert template.context_seed == ("Prefer primary sources.",)
    assert template.workspace_scope == "task"
    assert template.target_agent_id == "expert_researcher"


def test_in_memory_plan_store_creates_and_updates_plan() -> None:
    store = InMemoryPlanStore()
    plan = PlanState(
        plan_id="plan_1",
        objective="Review the SDK architecture.",
        owner_agent_id="leader",
        created_at=1.0,
        updated_at=1.0,
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Review multi-agent primitives.",
                required_capabilities=("architecture-review",),
            ),
        ),
        evidence=(
            EvidenceHandle(
                evidence_id="evidence_1",
                kind="text",
                summary="Initial notes.",
            ),
        ),
    )

    store.create_plan(plan)
    updated = plan.with_status("running", now=2.0)
    store.save_plan(updated)

    assert store.get_plan("plan_1") == updated
    assert store.list_plans("leader") == [updated]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_subagent_template_captures_execution_policy tests/multi/test_planner_runtime.py::test_in_memory_plan_store_creates_and_updates_plan -q
```

Expected: FAIL with `ModuleNotFoundError` for `agentos.multi.planner`.

- [ ] **Step 3: Implement dataclasses and store**

Create `src/agentos/multi/planner.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Literal, Mapping, Protocol
from uuid import uuid4

from agentos.multi.types import TaskHandle, TaskResult
from agentos.workspace import WorkspaceHandle, WorkspaceScope


PlanStatus = Literal["draft", "running", "completed", "failed", "cancelled"]
PlanStepStatus = Literal[
    "pending",
    "assigned",
    "running",
    "completed",
    "failed",
    "blocked",
    "cancelled",
]
EvidenceKind = Literal["text", "artifact", "task_result", "team_message", "external"]


class PlanError(RuntimeError):
    """planner 基础错误。"""


class PlanNotFoundError(PlanError):
    """plan 不存在。"""


class PlanStepNotFoundError(PlanError):
    """plan step 不存在。"""


@dataclass(frozen=True, slots=True)
class SubAgentTemplate:
    """可复用 subagent 执行模板。"""

    template_id: str
    name: str
    role: str
    capabilities: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ()
    context_seed: tuple[str, ...] = ()
    workspace_scope: WorkspaceScope = "task"
    target_agent_id: str | None = None
    timeout_seconds: float = 300


@dataclass(frozen=True, slots=True)
class EvidenceHandle:
    """plan 中引用的 evidence/artifact 句柄。"""

    evidence_id: str
    kind: EvidenceKind
    summary: str
    uri: str | None = None
    producer_agent_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanStep:
    """plan 中的一个可分配步骤。"""

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
class PlanAssignment:
    """step -> task/subagent 的分配记录。"""

    plan_id: str
    step_id: str
    template_id: str
    task_id: str
    target_agent_id: str
    created_at: float


@dataclass(frozen=True, slots=True)
class PlanState:
    """planner state 的 truth projection。"""

    plan_id: str
    objective: str
    owner_agent_id: str
    status: PlanStatus = "draft"
    steps: tuple[PlanStep, ...] = ()
    evidence: tuple[EvidenceHandle, ...] = ()
    assignments: tuple[PlanAssignment, ...] = ()
    created_at: float = 0
    updated_at: float = 0
    workspace: WorkspaceHandle | None = None

    def with_status(self, status: PlanStatus, *, now: float) -> "PlanState":
        return replace(self, status=status, updated_at=now)


class PlanStore(Protocol):
    """plan state 的 truth source。"""

    def create_plan(self, plan: PlanState) -> None:
        """创建 plan。"""

    def save_plan(self, plan: PlanState) -> None:
        """保存 plan。"""

    def get_plan(self, plan_id: str) -> PlanState | None:
        """返回 plan。"""

    def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        """列出 plans。"""


class InMemoryPlanStore:
    """线程安全的本地 plan store。"""

    def __init__(self) -> None:
        self._plans: dict[str, PlanState] = {}
        self._lock = RLock()

    def create_plan(self, plan: PlanState) -> None:
        with self._lock:
            if plan.plan_id in self._plans:
                raise ValueError(f"plan already exists: {plan.plan_id}")
            self._plans[plan.plan_id] = plan

    def save_plan(self, plan: PlanState) -> None:
        with self._lock:
            if plan.plan_id not in self._plans:
                raise PlanNotFoundError(plan.plan_id)
            self._plans[plan.plan_id] = plan

    def get_plan(self, plan_id: str) -> PlanState | None:
        with self._lock:
            return self._plans.get(plan_id)

    def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        with self._lock:
            plans = list(self._plans.values())
        if owner_agent_id is None:
            return plans
        return [plan for plan in plans if plan.owner_agent_id == owner_agent_id]
```

- [ ] **Step 4: Run model/store tests**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_subagent_template_captures_execution_policy tests/multi/test_planner_runtime.py::test_in_memory_plan_store_creates_and_updates_plan -q
```

Expected: PASS.

## Task 2: Add PlannerRuntime Create/Add Step

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Modify: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Add failing runtime create/add tests**

Append to `tests/multi/test_planner_runtime.py`:

```python
from agentos.multi.planner import PlannerRuntime


def test_planner_runtime_creates_plan_and_adds_steps() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    plan = runtime.create_plan(
        objective="Review agent-os.",
        owner_agent_id="leader",
    )
    updated = runtime.add_step(
        plan.plan_id,
        instruction="Review planner boundaries.",
        required_capabilities=("review",),
        template_id="reviewer",
    )

    assert plan.plan_id == "plan_1"
    assert updated.steps[0].step_id == "step_1"
    assert updated.steps[0].template_id == "reviewer"
    assert updated.steps[0].required_capabilities == ("review",)
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_planner_runtime_creates_plan_and_adds_steps -q
```

Expected: FAIL with missing `PlannerRuntime`.

- [ ] **Step 3: Implement create_plan and add_step**

Append to `src/agentos/multi/planner.py`:

```python
class PlannerRuntime:
    """plan state runtime，不直接执行 QueryLoop。"""

    def __init__(
        self,
        *,
        store: PlanStore,
        templates: tuple[SubAgentTemplate, ...] = (),
        coordinator: object | None = None,
        clock: object | None = None,
        id_factory: object | None = None,
    ) -> None:
        self.store = store
        self.templates = {template.template_id: template for template in templates}
        self.coordinator = coordinator
        self._clock = clock if callable(clock) else time.time
        self._id_factory = id_factory if callable(id_factory) else self._default_id

    def create_plan(
        self,
        *,
        objective: str,
        owner_agent_id: str,
        plan_id: str | None = None,
        workspace: WorkspaceHandle | None = None,
    ) -> PlanState:
        now = float(self._clock())
        plan = PlanState(
            plan_id=plan_id or str(self._id_factory("plan")),
            objective=objective,
            owner_agent_id=owner_agent_id,
            created_at=now,
            updated_at=now,
            workspace=workspace,
        )
        self.store.create_plan(plan)
        return plan

    def add_step(
        self,
        plan_id: str,
        *,
        instruction: str,
        required_capabilities: tuple[str, ...] = (),
        template_id: str | None = None,
    ) -> PlanState:
        plan = self._require_plan(plan_id)
        if template_id is not None:
            self._require_template(template_id)
        step = PlanStep(
            step_id=str(self._id_factory("step")),
            instruction=instruction,
            required_capabilities=tuple(required_capabilities),
            template_id=template_id,
        )
        updated = replace(
            plan,
            steps=plan.steps + (step,),
            updated_at=float(self._clock()),
        )
        self.store.save_plan(updated)
        return updated

    def _require_plan(self, plan_id: str) -> PlanState:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise PlanNotFoundError(plan_id)
        return plan

    def _require_template(self, template_id: str) -> SubAgentTemplate:
        try:
            return self.templates[template_id]
        except KeyError as error:
            raise KeyError(template_id) from error

    def _default_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"
```

- [ ] **Step 4: Run create/add tests**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_planner_runtime_creates_plan_and_adds_steps -q
```

Expected: PASS.

## Task 3: Assign Steps Through Coordinator Boundary

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Modify: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Add failing assignment tests**

Append to `tests/multi/test_planner_runtime.py`:

```python
from agentos.multi import TaskHandle


class FakeCoordinator:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, object]] = []

    def spawn(self, **kwargs: object) -> TaskHandle:
        self.spawn_calls.append(kwargs)
        return TaskHandle(
            task_id="task_spawn",
            mode="spawn",
            target_agent_id="subagent_1",
            status="queued",
        )

    def dispatch(self, **kwargs: object) -> TaskHandle:
        self.dispatch_calls.append(kwargs)
        return TaskHandle(
            task_id="task_dispatch",
            mode="dispatch",
            target_agent_id="expert_reviewer",
            status="queued",
        )


def test_planner_runtime_assigns_step_to_spawn_template() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
                allowed_tool_names=("read_file",),
                timeout_seconds=30,
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    plan = runtime.add_step(
        plan.plan_id,
        instruction="Review planner module.",
        template_id="reviewer",
    )

    assigned = runtime.assign_step(plan.plan_id, "step_1", template_id="reviewer")

    assert assigned.steps[0].status == "assigned"
    assert assigned.steps[0].task_id == "task_spawn"
    assert assigned.steps[0].assigned_agent_id == "subagent_1"
    assert assigned.assignments[0].task_id == "task_spawn"
    assert coordinator.spawn_calls[0]["allowed_tool_names"] == ("read_file",)
    assert coordinator.spawn_calls[0]["timeout_seconds"] == 30


def test_planner_runtime_assigns_step_to_dispatch_template() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                capabilities=("architecture-review",),
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(
        plan.plan_id,
        instruction="Review architecture.",
        required_capabilities=("architecture-review",),
        template_id="expert-reviewer",
    )

    assigned = runtime.assign_step(
        plan.plan_id,
        "step_1",
        template_id="expert-reviewer",
    )

    assert assigned.steps[0].task_id == "task_dispatch"
    assert assigned.steps[0].assigned_agent_id == "expert_reviewer"
    assert coordinator.dispatch_calls[0]["required_capabilities"] == (
        "architecture-review",
    )
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_planner_runtime_assigns_step_to_spawn_template tests/multi/test_planner_runtime.py::test_planner_runtime_assigns_step_to_dispatch_template -q
```

Expected: FAIL with missing `assign_step`.

- [ ] **Step 3: Implement assign_step**

Add methods to `PlannerRuntime`:

```python
    def assign_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        template_id: str,
    ) -> PlanState:
        if self.coordinator is None:
            raise RuntimeError("coordinator is required to assign plan steps")
        plan = self._require_plan(plan_id)
        template = self._require_template(template_id)
        step = self._require_step(plan, step_id)
        instruction = self._instruction_for_template(step, template)
        if template.target_agent_id is None:
            handle = self.coordinator.spawn(
                instruction=instruction,
                allowed_tool_names=template.allowed_tool_names,
                parent_agent_id=plan.owner_agent_id,
                timeout_seconds=template.timeout_seconds,
            )
        else:
            handle = self.coordinator.dispatch(
                instruction=instruction,
                required_capabilities=(
                    step.required_capabilities or template.capabilities
                ),
                parent_agent_id=plan.owner_agent_id,
                allowed_tool_names=template.allowed_tool_names,
                timeout_seconds=template.timeout_seconds,
            )
        assignment = PlanAssignment(
            plan_id=plan_id,
            step_id=step_id,
            template_id=template.template_id,
            task_id=handle.task_id,
            target_agent_id=handle.target_agent_id,
            created_at=float(self._clock()),
        )
        updated_step = replace(
            step,
            status="assigned",
            template_id=template.template_id,
            task_id=handle.task_id,
            assigned_agent_id=handle.target_agent_id,
        )
        updated = self._replace_step(
            plan,
            updated_step,
            assignments=plan.assignments + (assignment,),
        )
        self.store.save_plan(updated)
        return updated

    def _require_step(self, plan: PlanState, step_id: str) -> PlanStep:
        for step in plan.steps:
            if step.step_id == step_id:
                return step
        raise PlanStepNotFoundError(step_id)

    def _replace_step(
        self,
        plan: PlanState,
        step: PlanStep,
        *,
        assignments: tuple[PlanAssignment, ...] | None = None,
    ) -> PlanState:
        return replace(
            plan,
            status="running" if plan.status == "draft" else plan.status,
            steps=tuple(
                step if current.step_id == step.step_id else current
                for current in plan.steps
            ),
            assignments=plan.assignments if assignments is None else assignments,
            updated_at=float(self._clock()),
        )

    def _instruction_for_template(
        self,
        step: PlanStep,
        template: SubAgentTemplate,
    ) -> str:
        context = "\n".join(template.context_seed)
        if not context:
            return step.instruction
        return f"{context}\n\nTask: {step.instruction}"
```

- [ ] **Step 4: Run assignment tests**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_planner_runtime_assigns_step_to_spawn_template tests/multi/test_planner_runtime.py::test_planner_runtime_assigns_step_to_dispatch_template -q
```

Expected: PASS.

## Task 4: Record Evidence And Complete Steps

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Modify: `tests/multi/test_planner_runtime.py`

- [ ] **Step 1: Add failing evidence/completion tests**

Append to `tests/multi/test_planner_runtime.py`:

```python
def test_planner_runtime_records_evidence_and_completes_plan() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(plan.plan_id, instruction="Review planner.")

    evidence = runtime.record_evidence(
        plan.plan_id,
        step_ids=("step_1",),
        kind="text",
        summary="Planner boundary is clean.",
        uri="memory://evidence/1",
        producer_agent_id="worker",
    )
    updated = runtime.complete_step(
        plan.plan_id,
        "step_1",
        evidence_ids=(evidence.evidence_id,),
    )

    assert evidence.evidence_id == "evidence_1"
    assert updated.steps[0].status == "completed"
    assert updated.steps[0].evidence_ids == ("evidence_1",)
    assert updated.status == "completed"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_planner_runtime_records_evidence_and_completes_plan -q
```

Expected: FAIL with missing `record_evidence`.

- [ ] **Step 3: Implement evidence and completion methods**

Add to `PlannerRuntime`:

```python
    def record_evidence(
        self,
        plan_id: str,
        *,
        step_ids: tuple[str, ...] = (),
        kind: EvidenceKind,
        summary: str,
        uri: str | None = None,
        producer_agent_id: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> EvidenceHandle:
        plan = self._require_plan(plan_id)
        evidence = EvidenceHandle(
            evidence_id=str(self._id_factory("evidence")),
            kind=kind,
            summary=summary,
            uri=uri,
            producer_agent_id=producer_agent_id,
            metadata=dict(metadata or {}),
        )
        steps = plan.steps
        for step_id in step_ids:
            step = self._require_step(replace(plan, steps=steps), step_id)
            updated_step = replace(
                step,
                evidence_ids=step.evidence_ids + (evidence.evidence_id,),
            )
            steps = tuple(
                updated_step if current.step_id == step_id else current
                for current in steps
            )
        updated = replace(
            plan,
            steps=steps,
            evidence=plan.evidence + (evidence,),
            updated_at=float(self._clock()),
        )
        self.store.save_plan(updated)
        return evidence

    def complete_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        evidence_ids: tuple[str, ...] = (),
    ) -> PlanState:
        plan = self._require_plan(plan_id)
        step = self._require_step(plan, step_id)
        merged_evidence_ids = step.evidence_ids + tuple(
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in step.evidence_ids
        )
        updated_step = replace(
            step,
            status="completed",
            evidence_ids=merged_evidence_ids,
        )
        updated = self._replace_step(plan, updated_step)
        if all(step.status == "completed" for step in updated.steps):
            updated = replace(
                updated,
                status="completed",
                updated_at=float(self._clock()),
            )
            self.store.save_plan(updated)
        return updated
```

- [ ] **Step 4: Run evidence/completion tests**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py::test_planner_runtime_records_evidence_and_completes_plan -q
```

Expected: PASS.

## Task 5: Public API Exports

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add failing public API assertions**

In `tests/architecture/test_public_api.py`, extend `test_phase8_multi_agent_public_api_exports` multi list with:

```python
        "EvidenceHandle",
        "InMemoryPlanStore",
        "PlanAssignment",
        "PlanError",
        "PlanState",
        "PlanStep",
        "PlanStore",
        "PlannerRuntime",
        "SubAgentTemplate",
```

Extend the top-level `agentos` list with:

```python
        "EvidenceHandle",
        "InMemoryPlanStore",
        "PlanState",
        "PlanStep",
        "PlannerRuntime",
        "SubAgentTemplate",
```

- [ ] **Step 2: Run public API test to verify failure**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports -q
```

Expected: FAIL because names are not exported.

- [ ] **Step 3: Export names**

Import planner names in `src/agentos/multi/__init__.py` and add them to `__all__`.

Mirror stable planner names in `src/agentos/__init__.py` and add them to `__all__`.

- [ ] **Step 4: Run public API tests**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports -q
```

Expected: PASS.

## Task 6: Documentation Alignment

**Files:**
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`

- [ ] **Step 1: Update agent forms docs**

In `.claude/skills/agent-os/modules/agent-forms.md`, add or update:

```markdown
### Planner / Intent-Router Agent
**Available**: `PlannerRuntime`, `PlanState`, `PlanStep`, `SubAgentTemplate`, `EvidenceHandle`, `InMemoryPlanStore`, `AgentCoordinator` assignment boundary
**Missing after Phase 6A**: LLM-facing planner tools, automatic decomposition, DAG scheduler, persistent plan store, production retry/scheduling policy. Phase 6B implements the planner tools slice.
**Workaround after Phase 6C**: use `PlanStore` as the truth source and project only `plan_to_working_state_summary(plan)` into working state when a prompt needs plan awareness.
```

Move the Future Extensions row to:

```markdown
| Planner / Intent-Router Agent | Planner primitives are available after Phase 6A; Phase 6B adds tool-facing planner operations; automatic LLM decomposition, graph scheduling, and production plan store remain future work |
```

- [ ] **Step 2: Update multi-agent docs**

Add section:

```markdown
## Planner Runtime

`PlannerRuntime` stores `PlanState`, `PlanStep`, `SubAgentTemplate`, and `EvidenceHandle` values in `PlanStore`. It delegates execution through `AgentCoordinator` but does not mutate `QueryLoop` or share parent active messages with workers.

Use planner primitives and Phase 6B planner tools for intent-router and plan-and-execute patterns. Automatic decomposition, graph scheduling, and persistent plan stores remain future work.
```

- [ ] **Step 3: Update flow docs**

Update planner requirement/spec language to say planner primitives exist while
tools/decomposition/persistence remain app-owned or roadmap.

- [ ] **Step 4: Run docs drift search**

Run:

```bash
rg -n "No SDK planner|no SDK planner|app layer only|App-layer pattern only|no SDK planner state|planner templates remain|planner/templates future|team-planner-roadmap|No planner or intent router" .claude docs src tests -g "!docs/superpowers/plans/2026-06-12-planner-subagent-template-implementation-plan.md"
```

Expected: no stale capability claims. Docs should say planner primitives exist after Phase 6A and future work is tools/decomposition/DAG/persistent plan store.

## Task 7: Verification

**Files:** all changed files

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/multi/test_planner_runtime.py tests/architecture/test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compileall**

Run:

```bash
uv run python -m compileall -q src tests
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run final diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. Windows line-ending warnings are acceptable if no error lines are printed.

- [ ] **Step 5: Boundary search**

Run:

```bash
rg "agentos.multi.planner|PlannerRuntime|PlanState|SubAgentTemplate|AgentCoordinator|Redis|Postgres|A2A|workspace" src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py
```

Expected: no matches.

## Self-Review

Spec coverage:

- Templates and plan state: Task 1.
- Runtime create/add: Task 2.
- Assignment through coordinator: Task 3.
- Explicit persistent expert target preservation through real coordinator: execution addendum.
- Evidence and completion: Task 4.
- Public API: Task 5.
- Skill docs: Task 6.
- Verification and QueryLoop boundary: Task 7.

Placeholder scan:

- No implementation step contains "TBD", "TODO", or unspecified tests.
- Deferred items are explicit non-goals.

Type consistency:

- Names match the design spec: `SubAgentTemplate`, `EvidenceHandle`, `PlanStep`, `PlanAssignment`, `PlanState`, `PlanStore`, `InMemoryPlanStore`, `PlannerRuntime`.
