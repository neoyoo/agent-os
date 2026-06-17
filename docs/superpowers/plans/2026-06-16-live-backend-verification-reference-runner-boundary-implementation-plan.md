# Live Backend Verification Reference Runner Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned argv-only reference runner that invokes deployment-owned backend verification scripts and converts report path or stdout JSON into readiness evidence.

**Architecture:** Extend `agentos.deployment` beside the existing live backend verification evidence boundary. Keep the SDK at the invocation/evidence layer: no Nacos, Redis, Postgres, supervisor, session persistence, Docker, E2B, Kubernetes, systemd, migration, CI, alerting, or certification execution.

**Tech Stack:** Python dataclasses, subprocess with `shell=False`, pytest, existing public export tests, production readiness docs, AgentOS skill guidance.

---

### Task 1: Runner API

**Files:**
- Modify: `src/agentos/deployment.py`
- Modify: `src/agentos/__init__.py`
- Test: `tests/deployment/test_live_backend_verification_runner.py`
- Test: `tests/architecture/test_public_api.py`

- [ ] Write failing tests for `BackendVerificationReportImporter`,
  `BackendVerificationInvocationPlan`, `DeploymentLiveBackendVerificationRunResult`,
  `BackendVerificationRunner`, and `BackendVerificationCliRunner`.
- [ ] Run the tests and verify they fail because the API is missing.
- [ ] Implement report import from JSON objects containing a non-empty `records`
  list with snake_case and camelCase aliases.
- [ ] Implement the argv-only CLI runner with `shell=False`, bounded stdout/stderr
  summaries, report path import, stdout JSON import, timeout evidence, nonzero
  exit evidence, malformed report evidence, `env_keys`, and secret value
  redaction.
- [ ] Export the new API from `agentos.deployment` and top-level `agentos`.
- [ ] Run the runner and public API tests.

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

- [ ] Add failing docs tests for the live backend verification reference runner
  boundary.
- [ ] Update production readiness docs, objective audit, roadmap, and skill
  guidance to name the runner and keep concrete backend verification
  deployment-owned.
- [ ] Run docs tests.

### Task 3: Boundary Verification

**Files:**
- Verify: `src/agentos/runtime/query_loop.py`
- Verify: `src/agentos/runtime/async_query_loop.py`

- [ ] Run focused tests for deployment, public API, readiness docs, and audit
  docs.
- [ ] Scan query loops for live backend verification runner concepts.
- [ ] Run compile and diff hygiene checks.
