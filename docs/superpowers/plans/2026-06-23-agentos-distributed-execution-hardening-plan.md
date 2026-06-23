# AgentOS Distributed Execution Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining P1/P2 production correctness gaps in the distributed worker execution path.

**Architecture:** Keep AgentOS as an SDK boundary: the SDK owns task truth, claim leases, durable state transitions, and evidence contracts; deployments own process supervision, credentials, CI, and real infrastructure. Split worker capability routing from tool allowlists, fence every terminal state write with the active claim, and make backend write paths rollback-safe before widening planner dispatch hardening.

**Tech Stack:** Python dataclasses, in-memory `TaskTable`, Postgres-backed task store, Redis stream queue, pytest, uv.

---

### Task 1: Split Worker Capabilities From Tool Allowlists

**Files:**
- Modify: `src/agentos/multi/types.py`
- Modify: `src/agentos/multi/coordinator.py`
- Modify: `src/agentos/multi/tasks.py`
- Modify: `src/agentos/multi/postgres_tasks.py`
- Modify: `src/agentos/multi/serializers.py`
- Modify: `docs/public-api-inventory.json`
- Test: `tests/multi/test_task_store_contract.py`
- Test: `tests/multi/test_postgres_task_store.py`
- Test: `tests/multi/test_task_serializers.py`
- Test: `tests/multi/test_coordinator_dispatch.py`
- Test: `tests/multi/test_expert_runner.py`

- [ ] **Step 1: Write failing tests**

Add tests proving a task with `required_capabilities=("architecture-review",)` and `allowed_tool_names=("read_file",)` can be claimed by a worker with `capabilities=("architecture-review",)` and cannot be claimed by a worker with only `("read_file",)`.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
uv run pytest tests\multi\test_task_store_contract.py tests\multi\test_postgres_task_store.py tests\multi\test_task_serializers.py -q
```

Expected: failures show claim logic still reads `allowed_tool_names` as capabilities or serialization lacks `required_capabilities`.

- [ ] **Step 3: Add `TaskRequest.required_capabilities`**

Add a defaulted `required_capabilities: tuple[str, ...] = ()` field after `instruction`. Update coordinator dispatch construction to preserve the caller's `required_capabilities` separately from `allowed_tool_names`.

- [ ] **Step 4: Update claim logic**

Make `TaskTable._can_claim()` and `PostgresTaskStore.claim_task()` / `claim_queued()` match against `record.request.required_capabilities`, not `record.request.allowed_tool_names`.

- [ ] **Step 5: Update serialization and inventory**

Persist and load `required_capabilities` in task request JSON while keeping backward compatibility for records that do not contain the field. Update `docs/public-api-inventory.json` for `TaskRequest`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_task_store_contract.py tests\multi\test_postgres_task_store.py tests\multi\test_task_serializers.py tests\multi\test_coordinator_dispatch.py tests\multi\test_expert_runner.py tests\architecture\test_public_api.py -q
```

Expected: all selected tests pass.

### Task 2: Fence Claimed Cancellation And Define Claim Miss Handling

**Files:**
- Modify: `src/agentos/multi/coordinator.py`
- Modify: `src/agentos/multi/expert.py`
- Modify: `src/agentos/multi/tasks.py`
- Modify: `src/agentos/multi/postgres_tasks.py`
- Test: `tests/multi/test_expert_runner.py`
- Test: `tests/multi/test_task_store_contract.py`
- Test: `tests/multi/test_postgres_task_store.py`
- Test: `tests/multi/test_redis_pending_retry.py`

- [ ] **Step 1: Write failing cancel fence tests**

Add tests showing a claimed task with `cancel_requested_at` transitions to `cancelled` only when `worker_id` and `attempt` match the active claim.

- [ ] **Step 2: Write failing claim miss tests**

Add tests showing `ExpertAgentRunner.run_once()` does not silently drop in-memory deliveries and does not leave terminal/misrouted deliveries pending forever when `claim_task()` returns `None`.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```powershell
uv run pytest tests\multi\test_expert_runner.py tests\multi\test_task_store_contract.py -q
```

Expected: new tests fail on missing fence propagation or missing claim-miss policy.

- [ ] **Step 4: Propagate claim into cancel path**

Update `AgentCoordinator._handle_result_after_cancel_requested()` and its callers so claimed cancellation uses the same `worker_id` and `attempt` as completed/failed transitions.

- [ ] **Step 5: Add minimal claim miss policy**

For claim miss caused by already-terminal task, ACK the delivery. For non-terminal but unclaimable task, leave Redis pending for reclaim but preserve in-memory delivery through an explicit retry/dead-letter path or avoid draining until claimable.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_expert_runner.py tests\multi\test_task_store_contract.py tests\multi\test_postgres_task_store.py tests\multi\test_redis_pending_retry.py -q
```

Expected: all selected tests pass.

### Task 3: Add Postgres Task Store Rollback Safety

**Files:**
- Modify: `src/agentos/multi/postgres_tasks.py`
- Test: `tests/multi/test_postgres_task_store.py`

- [ ] **Step 1: Write failing rollback tests**

Add fake connection tests proving a failed write after an UPDATE calls rollback and does not call commit. Cover terminal transition with outbox insert failure.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
uv run pytest tests\multi\test_postgres_task_store.py -q
```

Expected: rollback expectations fail.

- [ ] **Step 3: Add rollback-aware write scope**

Ensure `_with_connection_scope` passes exception context to pool connections and calls rollback on any write exception for direct connections.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_postgres_task_store.py -q
```

Expected: all Postgres task store tests pass.

### Task 4: Rebaseline Planner Dispatch Fence

**Files:**
- Modify: `src/agentos/multi/planner.py`
- Test: `tests/multi/test_planner_runtime.py`
- Optional docs: `docs/production-readiness.md`

- [ ] **Step 1: Verify existing side-effect-before-save tests**

Find tests that currently assert dispatch side effects can happen before a claimed save failure. Decide whether they represent accepted behavior or a bug.

- [ ] **Step 2: Write failing test for desired fence**

If fixing in this iteration, add a test where claim is lost before pending recovery submit and assert no new external dispatch occurs.

- [ ] **Step 3: Implement minimal fence or document deferred outbox**

Either re-check exact live claim immediately before external dispatch, or document the next larger durable dispatch outbox design if the safe fix is too wide.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests\multi\test_planner_runtime.py -q
```

Expected: all planner runtime tests pass.

### Task 5: Verification, Commit, Push, And Objective Review

**Files:**
- No planned source changes beyond previous tasks.

- [ ] **Step 1: Run focused suites**

Run:

```powershell
uv run pytest tests\multi -q
uv run pytest tests\deployment -q
uv run pytest tests\architecture\test_public_api.py -q
```

- [ ] **Step 2: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests scripts
git diff --check
```

- [ ] **Step 3: Run live integration**

Run real Postgres/Redis integration with `AGENTOS_RUN_INTEGRATION=1` and the dev backend environment.

- [ ] **Step 4: Commit and push current development branch**

Commit on `review/agentos-sdk-architecture-20260611` and push to the same remote branch.

- [ ] **Step 5: Dispatch fresh objective review**

Send independent subagents only the new commit range and ask for objective P0/P1/P2 review without target-score prompting.
