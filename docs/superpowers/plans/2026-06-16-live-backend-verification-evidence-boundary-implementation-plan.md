# Live Backend Verification Evidence Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned live backend verification evidence boundary that consumes deployment-owned backend check records and blocks production readiness when required backend evidence is missing or failed.

**Architecture:** Implement generic record, gate report, and readiness profile dataclasses in `agentos.deployment`. Keep checks as consumed evidence only; no backend network calls, credentials, migrations, or CI execution live in the SDK.

**Tech Stack:** Python dataclasses, pytest, existing public export tests, production readiness docs, AgentOS skill guidance.

---

### Task 1: Evidence Boundary API

**Files:**
- Modify: `src/agentos/deployment.py`
- Modify: `src/agentos/__init__.py`
- Test: `tests/deployment/test_live_backend_verification.py`
- Test: `tests/architecture/test_public_api.py`

- [ ] Write failing tests for `BackendVerificationRecord`,
  `DeploymentLiveBackendVerificationGateReport`, and
  `DeploymentLiveBackendVerificationProfile`.
- [ ] Run the tests and verify they fail because the API is missing.
- [ ] Implement the minimal deployment evidence classes and default state-plane
  backend constant.
- [ ] Export the new API from `agentos.deployment` and top-level `agentos`.
- [ ] Run the deployment and public API tests.

### Task 2: Guidance And Audit

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/architecture.md`
- Modify: `.claude/skills/agent-os/modules/persistence.md`
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] Add failing docs tests for the live backend verification evidence
  boundary.
- [ ] Update production readiness docs, objective audit, roadmap, and skill
  guidance to name the boundary and keep concrete verification deployment-owned.
- [ ] Run docs tests.

### Task 3: Boundary Verification

**Files:**
- Verify: `src/agentos/runtime/query_loop.py`
- Verify: `src/agentos/runtime/async_query_loop.py`

- [ ] Run focused tests for deployment, public API, readiness docs, and audit
  docs.
- [ ] Scan query loops for live backend verification concepts.
- [ ] Run compile and diff hygiene checks.
