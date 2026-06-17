# A2A Egress URL Policy Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable SDK boundary for A2A outbound URL governance across discovery, JWKS, OIDC, operation clients, and the internal task bridge.

**Architecture:** Define a small protocol and two concrete policy classes in `src/agentos/channels/a2a.py`, then inject that policy into outbound A2A components before their transport calls. Keep policy optional for compatibility and keep runtime loops free of A2A/security-governance imports.

**Tech Stack:** Python dataclasses/protocols, urllib URL parsing, ipaddress literal-host checks, pytest.

---

### Task 1: Add Egress Policy Tests

**Files:**
- Create: `tests/channels/test_a2a_egress_url_policy.py`

- [ ] **Step 1: Write failing tests**

Create tests that import `A2AEgressPolicyError`,
`PublicHttpsA2AEgressUrlPolicy`, and `HostAllowListA2AEgressUrlPolicy`; assert
public HTTPS acceptance; assert rejection of HTTP, localhost, private literal IP
hosts, and suffix-confusion hosts; assert resolver/OIDC/JWKS/client/adapter
policies run before transport calls.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_egress_url_policy.py -q
```

Expected: fail because the new policy classes and constructor parameters are not
defined yet.

### Task 2: Implement Policy Boundary

**Files:**
- Modify: `src/agentos/channels/a2a.py`
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Add protocol and policies**

Add `A2AEgressPolicyError`, `A2AEgressUrlPolicy`,
`PublicHttpsA2AEgressUrlPolicy`, and `HostAllowListA2AEgressUrlPolicy` in
`a2a.py`. Use `urlparse` and `ipaddress.ip_address` for scheme/host and literal
IP checks.

- [ ] **Step 2: Inject validation**

Add optional `egress_url_policy` constructor parameters to the outbound A2A
components. Validate URLs before calling `transport.get_json`,
`transport.post_json`, or `transport.delete_json`.

- [ ] **Step 3: Run targeted GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_egress_url_policy.py -q
```

Expected: all tests pass.

### Task 3: Export API And Refresh Guidance

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Add public exports**

Export the new policy classes from `agentos.channels` and top-level `agentos`;
update public API tests.

- [ ] **Step 2: Update readiness language**

Move URL allow-list/public-host egress controls from pure gap wording into SDK
evidence while leaving DNS pinning, enterprise egress proxying, CA rollout,
tenant RBAC, and credential rotation as deployment/profile work.

- [ ] **Step 3: Run targeted docs/API tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_egress_url_policy.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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

- [ ] **Step 2: Compile and scan boundaries**

Run:

```powershell
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: compile passes; runtime scan has no matches; diff check exits 0 except
for existing CRLF warnings if present.
