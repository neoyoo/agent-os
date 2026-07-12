# Production Reference Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic production reference web agent example that proves the first AgentOS SDK release can be assembled from the SDK-owned service, runtime profile, state-plane, backend-probe, readiness, and planner primitives.

**Architecture:** The example composes existing SDK objects and exposes JSON-safe evidence. It uses local deterministic identity objects for reference-only backends where creating real clients would imply deployment ownership. The example never provisions or connects to Nacos, Redis, Postgres, Kubernetes/systemd, CI/CD, credentials, or sandbox isolation.

**Tech Stack:** Python dataclasses, AgentOS SDK reference objects, pytest, ASGI readiness helper tests.

---

### Task 1: Lock The Example Contract In Tests

**Files:**
- Create: `tests/examples/test_production_reference_web_agent.py`

- [ ] **Step 1: Write the failing test**

```python
def test_production_reference_web_agent_builds_reference_composition() -> None:
    from agentos.examples.production_reference_web_agent import (
        build_production_reference_web_agent,
    )

    example = build_production_reference_web_agent()

    assert example.service_reference.__class__.__name__ == "AgentServiceReference"
    assert example.runtime_profile.__class__.__name__ == "DistributedWebRuntimeProfile"
    assert example.state_plane_stack.__class__.__name__ == "ReferenceStatePlaneStack"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests\examples\test_production_reference_web_agent.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.examples.production_reference_web_agent'`.

### Task 2: Implement The Reference Example

**Files:**
- Create: `src/agentos/examples/production_reference_web_agent.py`

- [ ] **Step 1: Implement the minimal example module**

Create a dataclass named `ProductionReferenceWebAgentExample` with fields for
`service_reference`, `runtime_profile`, `state_plane_stack`, `probe_pack`,
`backend_verification`, `readiness_bundle`, `planner_primitive`, `app`, and
`metadata`.

- [ ] **Step 2: Implement builder and JSON evidence helpers**

Add `build_production_reference_web_agent()`, `as_dict()`,
`build_reference_app()`, `build_reference_readiness_evidence()`, and `main()`.

- [ ] **Step 3: Verify tests pass**

Run: `uv run pytest tests\examples\test_production_reference_web_agent.py -q`

Expected: PASS.

### Task 3: Update Release Documentation Evidence

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/quick-start.md`
- Modify: `README.md`
- Modify: `docs/quickstart.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/docs/test_production_hardening_docs.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Write failing doc tests**

Require the phrases `Phase 101: Production Reference Example`, `production
reference web agent`, `AgentServiceReference`, `DistributedWebRuntimeProfile`,
`Nacos/Redis/Postgres state plane`, `readiness endpoint`, `backend
verification`, `ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, `planner primitive`, `does not create backend
clients`, and `deployment-owned real infrastructure`.

- [ ] **Step 2: Run doc tests to verify they fail**

Run: `uv run pytest tests\docs\test_production_hardening_docs.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q`

Expected: FAIL until docs are updated.

- [ ] **Step 3: Update docs and skill guidance**

Add Phase 101 sections and link `src/agentos/examples/production_reference_web_agent.py`.

- [ ] **Step 4: Verify doc tests pass**

Run: `uv run pytest tests\docs\test_production_hardening_docs.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q`

Expected: PASS.

### Task 4: Release Verification

**Files:**
- No new files unless verification reveals a scoped issue.

- [ ] **Step 1: Run targeted tests**

Run:

```powershell
uv run pytest tests\examples\test_production_reference_web_agent.py -q
uv run pytest tests\docs\test_production_hardening_docs.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
uv run pytest tests\architecture\test_public_api.py -q
```

- [ ] **Step 2: Run compile and diff checks**

Run:

```powershell
uv run python -m compileall -q src tests
git diff --check
rg -n "ReferenceLiveBackendProbe|REFERENCE_LIVE_BACKEND|ReferenceStatePlane|state plane|readiness|planner|team|A2A|sandbox|worker supervisor|production_design_constraints|release hardening|production reference" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: compile and diff checks pass. Boundary scan returns no matches.

- [ ] **Step 3: Run full suite**

Run: `uv run pytest -q`

Expected: full suite passes.
