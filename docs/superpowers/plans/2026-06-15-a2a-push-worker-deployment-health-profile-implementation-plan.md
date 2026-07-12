# A2A Push Worker Deployment Health Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-facing A2A push worker health profile that turns daemon health into JSON-safe health/readiness checks for ASGI hosts and service supervisors.

**Architecture:** Keep the existing daemon and health policy unchanged. Add a small profile class in `src/agentos/channels/a2a_operations.py` that consumes `A2APushNotificationDaemon.health()`, serializes the report, and exposes `health_check()`, `readiness_check()`, and `readiness_metadata()` without changing default ASGI `/v1/health` behavior.

**Tech Stack:** Python dataclasses, literal status types, pytest, existing ASGI readiness checks.

---

### Task 1: Add RED Tests

**Files:**
- Modify: `tests/channels/test_a2a_push_notification_daemon.py`
- Modify: `tests/channels/test_asgi_app.py`

- [ ] **Step 1: Write profile payload/readiness tests**

Add tests that instantiate `A2APushNotificationDeploymentProfile`, verify
`health_payload()`, `health_check()`, `readiness_check()`, conservative default
readiness mapping, and custom degraded readiness mapping.

- [ ] **Step 2: Write ASGI wiring test**

Add a test that passes `profile.readiness_check` into
`AsgiAgentApp(readiness_checks={"a2a_push_worker": ...})` and verifies
`GET /v1/ready` returns `ready` after the daemon has a healthy run.

- [ ] **Step 3: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_push_notification_daemon.py tests\channels\test_asgi_app.py -q
```

Expected: fail because `A2APushNotificationDeploymentProfile` does not exist.

### Task 2: Implement Profile

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Add `A2APushNotificationDeploymentProfile`**

Add a frozen dataclass with:

- `daemon`
- `policy`
- `probe_name`
- `ready_statuses`

Validate `probe_name` is non-empty and `ready_statuses` is non-empty.

- [ ] **Step 2: Add payload methods**

Implement:

- `health_payload(now=None) -> dict[str, object]`
- `health_check(now=None) -> dict[str, object]`
- `readiness_check(now=None) -> dict[str, object]`
- `readiness_metadata() -> dict[str, object]`

`readiness_check()` should normalize `status` to `ok` or `failed` and preserve
the original health status under `health_status`.

- [ ] **Step 3: Run targeted GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_push_notification_daemon.py tests\channels\test_asgi_app.py -q
```

Expected: selected tests pass.

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

Expose `A2APushNotificationDeploymentProfile` from `agentos.channels` and
top-level `agentos`, then update public API tests.

- [ ] **Step 2: Refresh docs and readiness**

Move A2A push worker health endpoint automation from a missing gap to an SDK
profile evidence item. Keep process supervision, alerting, credentials,
DNS/egress, CA rollout, tenant RBAC, and external conformance execution outside
the SDK boundary.

- [ ] **Step 3: Run docs/API tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_push_notification_daemon.py tests\channels\test_asgi_app.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: selected tests pass.

### Task 4: Final Verification

**Files:**
- No new code files.

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
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: compile passes; runtime scan has no matches; diff check exits 0
except for existing CRLF warnings if present.
