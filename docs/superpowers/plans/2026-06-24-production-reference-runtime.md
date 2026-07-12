# Production Reference Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the remaining P2 production-readiness work into small SDK contract and reference-runtime increments that validate AgentOS without turning the SDK into a deployment platform.

**Architecture:** Keep SDK-owned contracts in reusable test/support modules, keep live backend checks as opt-in integration gates, and keep production infrastructure wiring in examples/reference docs. Start with adapter contract kits, then prove the contracts through a minimal distributed planner/worker flow, then add worker lifecycle evidence primitives, and only then broaden the reference deployment.

**Tech Stack:** Python dataclasses, Protocols, pytest, uv, in-memory AgentOS adapters, optional Postgres/Redis live integration, existing Nacos/Redis/Postgres reference state-plane docs.

---

## Milestone Boundaries

This plan has four tracks, but execution must stay incremental:

- Track A: Adapter Contract Hardening. SDK-owned, first priority.
- Track B: Distributed E2E Harness. SDK-owned tests plus opt-in live backend gate.
- Track C: Worker Lifecycle Minimal Loop. SDK-owned evidence primitives only.
- Track D: Reference Deployment Example. Example/reference-owned, no platform claims.

Do not implement Kubernetes, systemd, autoscaling, distributed quota, billing, sandbox isolation, TLS gateway, tenant directory, or official external certification execution in this plan. Those remain deployment-owned.

### Task 1: Extract TaskStore Contract Kit

**Files:**
- Create: `src/agentos/testing/__init__.py`
- Create: `src/agentos/testing/contracts/__init__.py`
- Create: `src/agentos/testing/contracts/task_store.py`
- Modify: `tests/multi/test_task_store_contract.py`
- Modify: `tests/multi/test_postgres_task_store.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/api-stability.md`
- Modify: `docs/public-api-inventory.json`

- [x] **Step 1: Write the failing contract-kit usage test for in-memory TaskTable**

Move no production code yet. Add a new test in `tests/multi/test_task_store_contract.py` that imports a reusable function which does not exist yet:

```python
from agentos.testing.contracts.task_store import run_task_store_contract


def test_task_table_satisfies_reusable_task_store_contract() -> None:
    run_task_store_contract(lambda: TaskTable())
```

- [x] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
uv run pytest tests\multi\test_task_store_contract.py::test_task_table_satisfies_reusable_task_store_contract -q
```

Expected: import failure for `agentos.testing.contracts.task_store`.

- [x] **Step 3: Add the reusable TaskStore contract runner**

Create `src/agentos/testing/__init__.py`,
`src/agentos/testing/contracts/__init__.py`, and
`src/agentos/testing/contracts/task_store.py` with helper builders and a
`run_task_store_contract(factory)` function. The runner must verify these
behavior groups against any fresh `TaskStore` instance:

```python
from __future__ import annotations

from collections.abc import Callable

from agentos.multi import TaskRecord, TaskRequest, TaskResult
from agentos.multi.task_store import TaskStore


def contract_record(
    task_id: str = "contract_task_1",
    *,
    target_agent_id: str = "expert",
    required_capabilities: tuple[str, ...] = ("architecture-review",),
    allowed_tool_names: tuple[str, ...] = ("read_file",),
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id=target_agent_id,
        request=TaskRequest(
            task_id=task_id,
            instruction="Review architecture.",
            required_capabilities=required_capabilities,
            allowed_tool_names=allowed_tool_names,
        ),
        status="queued",
        created_at=1.0,
        deadline_at=60.0,
    )


def contract_result(task_id: str, status: str = "completed") -> TaskResult:
    return TaskResult(
        task_id=task_id,
        status=status,  # type: ignore[arg-type]
        summary=f"{status} result",
    )


def run_task_store_contract(factory: Callable[[], TaskStore]) -> None:
    _assert_exact_claim_target_and_capability_fences(factory)
    _assert_lease_reclaim_and_terminal_write_fences(factory)
    _assert_cancel_and_result_consumption(factory)


def _assert_exact_claim_target_and_capability_fences(
    factory: Callable[[], TaskStore],
) -> None:
    store = factory()
    original = contract_record()
    store.create(original)

    wrong_target = store.claim_task(
        original.task_id,
        worker_id="worker_wrong_target",
        target_agent_id="other_expert",
        capabilities=("architecture-review",),
        lease_expires_at=20.0,
        now=2.0,
    )
    wrong_capability = store.claim_task(
        original.task_id,
        worker_id="worker_wrong_capability",
        target_agent_id="expert",
        capabilities=("read_file",),
        lease_expires_at=20.0,
        now=2.0,
    )
    claim = store.claim_task(
        original.task_id,
        worker_id="worker_ok",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=20.0,
        now=2.0,
    )

    assert wrong_target is None
    assert wrong_capability is None
    assert claim is not None
    assert claim.task_id == original.task_id
    stored = store.get(original.task_id)
    assert stored is not None
    assert stored.status == "running"
    assert stored.worker_id == "worker_ok"
    assert stored.request.allowed_tool_names == ("read_file",)


def _assert_lease_reclaim_and_terminal_write_fences(
    factory: Callable[[], TaskStore],
) -> None:
    store = factory()
    store.create(contract_record("contract_task_lease"))
    first = store.claim_task(
        "contract_task_lease",
        worker_id="worker_a",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=3.0,
        now=2.0,
    )
    second = store.claim_task(
        "contract_task_lease",
        worker_id="worker_b",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=8.0,
        now=4.0,
    )
    assert first is not None
    assert second is not None
    assert second.attempt == 2

    stale_result = contract_result("contract_task_lease")
    assert store.mark_completed(
        "contract_task_lease",
        stale_result,
        worker_id="worker_a",
        attempt=1,
        now=5.0,
    ) is False
    assert store.mark_completed(
        "contract_task_lease",
        stale_result,
        worker_id="worker_b",
        attempt=2,
        now=6.0,
    ) is True
    stored = store.get("contract_task_lease")
    assert stored is not None
    assert stored.status == "completed"
    assert stored.result == stale_result


def _assert_cancel_and_result_consumption(factory: Callable[[], TaskStore]) -> None:
    store = factory()
    store.create(contract_record("contract_task_cancel"))
    assert store.request_cancel("contract_task_cancel", now=3.0) is True
    stored = store.get("contract_task_cancel")
    assert stored is not None
    assert stored.status == "cancelled"
    assert stored.result is not None
    assert stored.result.status == "cancelled"

    store.create(contract_record("contract_task_result"))
    claim = store.claim_task(
        "contract_task_result",
        worker_id="worker_result",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=20.0,
        now=4.0,
    )
    assert claim is not None
    assert store.mark_completed(
        "contract_task_result",
        contract_result("contract_task_result"),
        worker_id=claim.worker_id,
        attempt=claim.attempt,
        now=5.0,
    ) is True
    results = store.consume_results_for_agent("parent")
    assert [result.task_id for result in results] == ["contract_task_result"]
    assert store.consume_results_for_agent("parent") == []
```

- [x] **Step 4: Run the in-memory contract test and verify it passes**

Run:

```powershell
uv run pytest tests\multi\test_task_store_contract.py::test_task_table_satisfies_reusable_task_store_contract -q
```

Expected: pass.

- [x] **Step 5: Add PostgresTaskStore fake-backed contract usage**

In `tests/multi/test_postgres_task_store.py`, add a test that uses the existing fake connection/fake store setup and calls `run_task_store_contract(...)` with a fresh `PostgresTaskStore(dsn="postgresql://unused", connection=FakeConnection())`.

If the existing fake connection lacks SQL branches for one of the reusable contract behaviors, add only the minimal fake SQL branch needed by the contract. Do not weaken the contract.

- [x] **Step 6: Run focused TaskStore contract tests**

Run:

```powershell
uv run pytest tests\multi\test_task_store_contract.py tests\multi\test_postgres_task_store.py -q
```

Expected: all selected tests pass.

- [x] **Step 7: Document the adapter contract kit**

In `docs/production-readiness.md`, add a short subsection under Direct Task Claim Safety:

```markdown
### Adapter Contract Test Kits

Third-party task-store adapters should run the reusable TaskStore contract tests
from `agentos.testing.contracts.task_store` before claiming production
compatibility. The contract covers target-agent fencing, capability matching,
lease reclaim, claimed terminal writes, cancellation convergence, and
single-consumer result consumption.
```

- [x] **Step 8: Govern the testing support API**

Add `agentos.testing` and `agentos.testing.contracts` to
`docs/api-stability.md` and `docs/public-api-inventory.json`. Keep these
helpers out of the root `agentos` facade.

- [x] **Step 9: Run docs and contract verification**

Run:

```powershell
uv run pytest tests\multi\test_task_store_contract.py tests\multi\test_postgres_task_store.py tests\architecture\test_public_api.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all selected tests pass.

### Task 2: Extract AgentMessageQueue Contract Kit

**Files:**
- Create: `src/agentos/testing/contracts/message_queue.py`
- Modify: `src/agentos/testing/__init__.py`
- Modify: `src/agentos/testing/contracts/__init__.py`
- Modify: `tests/multi/test_message_queue_contract.py`
- Modify: `tests/multi/test_redis_message_queue.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/public-api-inventory.json`

- [x] **Step 1: Write failing contract-kit usage test for AgentInbox**

Add to `tests/multi/test_message_queue_contract.py`:

```python
from agentos.testing.contracts.message_queue import run_agent_message_queue_contract


def test_agent_inbox_satisfies_reusable_message_queue_contract() -> None:
    run_agent_message_queue_contract(lambda: AgentInbox())
```

- [x] **Step 2: Run focused failure**

Run:

```powershell
uv run pytest tests\multi\test_message_queue_contract.py::test_agent_inbox_satisfies_reusable_message_queue_contract -q
```

Expected: import failure for `agentos.testing.contracts.message_queue`.

- [x] **Step 3: Add reusable message queue contract runner**

Create `src/agentos/testing/contracts/message_queue.py`. It must verify:

- `create_inbox` is idempotent enough for test setup.
- `send` returns a delivery id.
- `collect` returns `QueueDelivery` with the original envelope.
- `ack` returns `True` once and `False` for duplicate ack.
- `requeue` returns a drained delivery to the same agent queue when the adapter supports explicit local requeue.
- `wait(agent_id, timeout=0.01)` reports deliverability without losing the delivery.

- [x] **Step 4: Run in-memory queue contract**

Run:

```powershell
uv run pytest tests\multi\test_message_queue_contract.py -q
```

Expected: pass.

- [x] **Step 5: Add Redis fake-backed contract usage**

In `tests/multi/test_redis_message_queue.py`, add a test that constructs `RedisAgentMessageQueue` with the existing fake Redis client and runs `run_agent_message_queue_contract(...)`.

If fake Redis cannot support blocking wait, configure the contract with `require_requeue=False` or `supports_wait_matching=False` through explicit keyword arguments in the runner. Do not require a real Redis server for this unit test.

- [x] **Step 6: Run queue focused tests**

Run:

```powershell
uv run pytest tests\multi\test_message_queue_contract.py tests\multi\test_redis_message_queue.py tests\multi\test_redis_pending_retry.py -q
```

Expected: all selected tests pass.

- [x] **Step 7: Document message queue adapter contract**

Extend the Adapter Contract Test Kits subsection to mention `agentos.testing.contracts.message_queue` and its coverage: send/collect delivery, ack idempotency, optional requeue, wait semantics, and filtered collect retention.

### Task 3: PlanStore And PlanClaimStore Contract Audit

**Files:**
- Create: `src/agentos/testing/contracts/plan_store.py`
- Create: `src/agentos/testing/contracts/plan_claim_store.py`
- Modify: `src/agentos/testing/__init__.py`
- Modify: `src/agentos/testing/contracts/__init__.py`
- Create: `tests/multi/test_plan_store_contract.py`
- Create: `tests/multi/test_plan_claim_store_contract.py`
- Modify: `tests/multi/test_planner_runtime.py`
- Modify: `tests/multi/test_postgres_plan_store.py`
- Modify: `tests/multi/test_postgres_plan_claim_store.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/public-api-inventory.json`

- [x] **Step 1: Audit existing behavior**

Read `src/agentos/multi/planner.py`, `src/agentos/multi/postgres_plan.py`, `tests/multi/test_planner_runtime.py`, `tests/multi/test_postgres_plan_store.py`, and `tests/multi/test_postgres_plan_claim_store.py`.

- [x] **Step 2: Add minimal PlanStore reusable contract**

Create a contract runner that covers save/load, list by owner, compare-and-save conflict, and claim-guarded save when supported.

- [x] **Step 3: Add minimal PlanClaimStore reusable contract**

Create a contract runner that covers claim, busy result, same-worker refresh, expired claim takeover, exact release, and expired sweep.

- [x] **Step 4: Wire in-memory and Postgres fake tests**

Use `InMemoryPlanStore`, `InMemoryPlanClaimStore`, `PostgresPlanStore`, and `PostgresPlanClaimStore` existing fake connections to run the new contract runners.

- [x] **Step 5: Run planner store tests**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_postgres_plan_store.py tests\multi\test_postgres_plan_claim_store.py -q
```

Expected: all selected tests pass.

### Task 4: Minimal Distributed Planner Worker E2E Harness

**Files:**
- Create: `tests/integration/test_distributed_planner_worker_flow.py`
- Modify: `tests/integration/test_live_backends.py` only if shared fixtures can be extracted without changing behavior.
- Modify: `docs/production-readiness.md`

- [x] **Step 1: Add skipped-by-default live test skeleton**

Create a pytest integration test marked with the existing live backend skip convention. The first test should skip unless `AGENTOS_RUN_INTEGRATION=1` and required Postgres/Redis environment variables are configured.

- [x] **Step 2: Build the smallest real flow**

Use real `PostgresTaskStore` and `RedisAgentMessageQueue`, but keep planner state in memory for the first slice. Dispatch one task to one `ExpertAgentRunner`, run `run_once`, and assert the parent collects one terminal result.

- [x] **Step 3: Add crash/recovery slice**

Simulate a delivery that is claimed but not acked, call Redis pending reclaim, and assert a later runner can finish or skip according to TaskStore truth.

- [x] **Step 4: Add planner pending-dispatch recovery slice**

Use `PlannerRuntime.recover_pending_dispatches(...)` with a real `PostgresTaskStore` and existing planner store primitives when possible. Keep the test narrow: one pending assignment, one recovery call, one submitted evidence result.

- [ ] **Step 5: Run live integration gate**

Run with local/dev tunnel environment:

```powershell
uv run pytest tests\integration\test_live_backends.py tests\integration\test_distributed_planner_worker_flow.py -q
```

Expected without env: skipped. Expected with env: pass.

### Task 5: Worker Lifecycle Minimal Evidence Loop

**Files:**
- Modify: `src/agentos/worker_supervisor.py` or the existing worker lifecycle module found during audit.
- Modify: `src/agentos/multi/expert.py`
- Modify: `src/agentos/multi/tasks.py`
- Test: `tests/test_worker_supervisor.py` or existing lifecycle test file.
- Test: `tests/multi/test_expert_runner.py`
- Docs: `docs/production-readiness.md`

- [x] **Step 1: Write failing tests for heartbeat evidence**

Add tests proving a worker process state can record a heartbeat timestamp without exposing secrets or inheriting platform-specific supervisor behavior.

- [x] **Step 2: Add minimal heartbeat evidence primitive**

Add only SDK-owned data/evidence fields and local reference methods. Do not add Kubernetes/systemd/autoscaling integrations.

- [x] **Step 3: Write failing tests for graceful drain**

Add tests proving `ExpertAgentRunner.stop(...)` stops accepting new work, waits for the current `run_once` critical section to become idle, and does not drop an already collected delivery silently.

- [x] **Step 4: Implement graceful drain behavior**

Keep implementation inside `ExpertAgentRunner` and existing task lease APIs. Do not add a new process manager.

- [x] **Step 5: Run lifecycle focused tests**

Run:

```powershell
uv run pytest tests\multi\test_expert_runner.py tests\test_worker_supervisor.py -q
```

Expected: all selected tests pass.

### Task 6: Reference Deployment Example Rebaseline

**Files:**
- Modify: `src/agentos/examples/production_reference_web_agent.py`
- Modify: `tests/examples/test_production_reference_web_agent.py`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/production-readiness.md`
- Modify: `docs/release-backlog.md`

- [ ] **Step 1: Add reference topology test**

Extend existing production reference example tests to assert the example names the state plane components, planner worker, expert worker, readiness endpoint, and backend verification evidence without claiming to provision infrastructure.

- [ ] **Step 2: Update reference example metadata**

Expose the minimal topology evidence from the example. Keep secrets, migrations, process supervisor, and backend startup deployment-owned.

- [ ] **Step 3: Update docs and skill guidance**

Document that the reference runtime is a validation example and copyable composition, not a hosted platform.

- [ ] **Step 4: Run reference verification**

Run:

```powershell
uv run pytest tests\examples\test_production_reference_web_agent.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all selected tests pass.

### Task 7: Verification, Review, Commit, Push

**Files:**
- No planned source files beyond previous tasks.

- [ ] **Step 1: Run focused suites**

Run:

```powershell
uv run pytest tests\multi -q
uv run pytest tests\integration -q
uv run pytest tests\architecture\test_public_api.py -q
uv run pytest tests\docs -q
```

- [ ] **Step 2: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests scripts
git diff --check
```

- [ ] **Step 3: Refresh local release evidence**

After commit, regenerate ignored `docs/release-evidence.json` with the new HEAD so local release evidence tests remain aligned.

- [ ] **Step 4: Push current branch**

Push to:

```powershell
git push origin review/agentos-sdk-architecture-20260611
```

- [ ] **Step 5: Fresh objective review**

Dispatch fresh reviewers with only the new commit range. Ask for objective P0/P1/P2 findings and do not mention a target score.
