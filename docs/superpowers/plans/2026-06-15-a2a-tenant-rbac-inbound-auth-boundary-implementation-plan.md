# A2A Tenant RBAC Inbound Auth Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a claims-backed A2A tenant RBAC policy so enterprise deployments can enforce tenant-aware operation and resource authorization after JWT/OIDC peer authentication.

**Architecture:** Add a small rule dataclass and policy class in `src/agentos/channels/a2a.py`. The policy delegates identity proof to an existing claims policy, extracts tenant/role/scope attributes from verified claims, and evaluates declarative rules. Keep tenant directories, IAM sync, and role assignment lifecycle outside the SDK.

**Tech Stack:** Python dataclasses, Mapping claim parsing, pytest, existing A2A inbound auth protocols.

---

### Task 1: Add RED Tests

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Add operation authorization tests**

Add tests for `ClaimsTenantRbacA2AInboundAuthPolicy` that verify a valid JWT
with matching tenant and role can call `message/send`, while a valid JWT from a
different tenant is rejected with `A2AInboundAuthError("unauthorized peer")`.

- [ ] **Step 2: Add resource authorization tests**

Add tests that verify `authorize_resource(... task_id="task_1")` requires a
matching tenant, operation, scope, and task resource.

- [ ] **Step 3: Add custom claims tests**

Add tests proving custom tenant/role/scope claim names work and whitespace
scope strings split correctly.

- [ ] **Step 4: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: fail because the tenant RBAC rule and policy are not implemented.

### Task 2: Implement Tenant RBAC Policy

**Files:**
- Modify: `src/agentos/channels/a2a.py`

- [ ] **Step 1: Add `A2ATenantRbacRule`**

Add a frozen dataclass with:

- `tenant_id: str`
- `operations: tuple[str, ...]`
- `roles: tuple[str, ...] = ()`
- `scopes: tuple[str, ...] = ()`
- `resources: tuple[tuple[str, str], ...] = ()`

Validate tenant id and operations are not empty.

- [ ] **Step 2: Add `ClaimsTenantRbacA2AInboundAuthPolicy`**

Implement:

- `authorize(headers)`
- `authorize_operation(headers, operation=...)`
- `authorize_resource(headers, operation=..., task_id=None, resource_type=None, resource_id=None)`

The policy should call `claims_policy.claims_for_headers(headers)`, extract
attributes from claims.raw, and evaluate rules with wildcard support.

- [ ] **Step 3: Keep errors generic**

Every denied path should raise `A2AInboundAuthError("unauthorized peer")`.
`__repr__` must redact the claims policy.

- [ ] **Step 4: Run targeted GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
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

Expose `A2ATenantRbacRule` and `ClaimsTenantRbacA2AInboundAuthPolicy` from
`agentos.channels` and top-level `agentos`; update public API tests.

- [ ] **Step 2: Refresh readiness/docs**

Move tenant RBAC mapping from a pure gap to an SDK policy boundary while keeping
tenant directory, role assignment lifecycle, IdP administration, and credential
rotation deployment-owned.

- [ ] **Step 3: Run docs/API tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: compile passes; runtime scan has no matches; diff check exits 0
except for existing CRLF warnings if present.
