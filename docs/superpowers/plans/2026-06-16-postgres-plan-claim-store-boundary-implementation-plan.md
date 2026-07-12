# Postgres Plan Claim Store Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Postgres-backed `PlanClaimStore` adapter for durable multi-node planner claim/lease coordination.

**Architecture:** Implement `PostgresPlanClaimStore` beside `PostgresPlanStore` in `src/agentos/multi/postgres_plan.py`. Reuse the existing connection/pool pattern, return `PlanClaimResult` and `PlanClaimRecord`, and keep production lock governance deployment-owned. The adapter does not run scheduler loops and does not touch runtime query loops.

**Tech Stack:** Python dataclasses/protocols already in `agentos.multi.planner`, psycopg-compatible connection protocols, JSONB migration docs, pytest fake-connection tests.

---

### Task 1: RED Tests

**Files:**
- Create: `tests/multi/test_postgres_plan_claim_store.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] Add tests for claim success, busy claims, renewal/takeover generation,
      release ownership, validation, migration shape, and public API exports.
- [ ] Run:
      `uv run pytest tests\multi\test_postgres_plan_claim_store.py tests\architecture\test_public_api.py -q`
- [ ] Expected: fail because `PostgresPlanClaimStore` is not defined.

### Task 2: Adapter Implementation

**Files:**
- Modify: `src/agentos/multi/postgres_plan.py`
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Create: `docs/migrations/2026-06-16-postgres-plan-claims.sql`

- [ ] Implement `PostgresPlanClaimStore` with `claim_plan`, `release_plan`,
      `get_claim`, `from_pool`, validation helpers, row conversion, commit, and
      close methods.
- [ ] Use an `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE` claim query so
      another worker cannot overwrite an unexpired lease.
- [ ] Add a migration for `agentos_plan_claims` with owner/expiry and
      worker/expiry indexes.
- [ ] Export the adapter from `agentos.multi` and top-level `agentos`.
- [ ] Run:
      `uv run pytest tests\multi\test_postgres_plan_claim_store.py tests\architecture\test_public_api.py -q`
- [ ] Expected: pass.

### Task 3: Readiness And Guidance

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: relevant docs tests.

- [ ] Update tests to require `PostgresPlanClaimStore`,
      `2026-06-16-postgres-plan-claims.sql`, and Phase 75 evidence.
- [ ] Update docs to say durable Postgres claim storage is SDK-owned while
      leader election, global fairness, stale-lease sweeps, credential policy,
      migration execution, tenant auth, and live backend verification remain
      deployment-owned.
- [ ] Increase the objective coverage estimate only if the new evidence
      meaningfully closes a production blocker.
- [ ] Run focused readiness/docs tests.

### Task 4: Verification

- [ ] Run:
      `uv run pytest tests\multi\test_postgres_plan_claim_store.py tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\multi\test_planner_scheduler_daemon.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q`
- [ ] Run:
      `uv run pytest -q`
- [ ] Run:
      `uv run python -m compileall -q src tests`
- [ ] Run the runtime boundary scan:
      `rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC|PlannerSchedulerDaemon|PlanClaim|PostgresPlanClaimStore" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py`
- [ ] Expected boundary scan: exit code `1`, no output.
- [ ] Run:
      `git diff --check`
