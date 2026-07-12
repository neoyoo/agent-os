# A2A Push Worker Health Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stable health report boundary for A2A push notification daemons so deployment supervisors can classify worker state without interpreting raw daemon internals.

**Architecture:** Add immutable health dataclasses and a policy class in `src/agentos/channels/a2a_operations.py`. The policy consumes `A2APushNotificationDaemonState`; the daemon exposes `health(policy=None, now=None)` as a convenience wrapper while keeping process supervision and alerting outside the SDK.

**Tech Stack:** Python dataclasses, literal status types, pytest.

---

### Task 1: Add Health Policy Tests

**Files:**
- Modify: `tests/channels/test_a2a_push_notification_daemon.py`

- [ ] **Step 1: Write failing tests**

Add tests for `A2APushNotificationHealthPolicy` using constructed
`A2APushNotificationDaemonState` records. Cover unstarted, healthy, stale
unhealthy, degraded recent errors, unhealthy recent errors, stopped, and
`daemon.health()` delegation.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_push_notification_daemon.py -q
```

Expected: fail because the health policy classes do not exist yet.

### Task 2: Implement Health Boundary

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Add dataclasses and policy**

Add `A2APushNotificationHealthStatus`,
`A2APushNotificationHealthReport`, and `A2APushNotificationHealthPolicy`.
The policy should count recent daemon errors inside
`error_window_seconds`, detect stale workers with `max_stale_seconds`, and
return immutable reports.

- [ ] **Step 2: Add daemon helper**

Add `A2APushNotificationDaemon.health(policy=None, now=None)` that snapshots
`self.state()` and delegates to the supplied or default health policy.

- [ ] **Step 3: Run targeted GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_push_notification_daemon.py -q
```

Expected: all tests pass.

### Task 3: Export And Document

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Export public API**

Expose the health status/report/policy classes from `agentos.channels` and
top-level `agentos`; update public API tests.

- [ ] **Step 2: Refresh readiness and docs**

Move "worker monitoring" from a completely unstructured gap to an SDK health
projection evidence item while keeping alerting, restart supervision, and
orchestration deployment-owned.

- [ ] **Step 3: Run targeted docs/API tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_push_notification_daemon.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all selected tests pass.

### Task 4: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run full suite**

Run:

```powershell
uv run pytest -q
```

Expected: full suite passes.

- [ ] **Step 2: Compile and boundary scan**

Run:

```powershell
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: compile passes; runtime scan has no matches; diff check exits 0 except
for existing CRLF warnings if present.
