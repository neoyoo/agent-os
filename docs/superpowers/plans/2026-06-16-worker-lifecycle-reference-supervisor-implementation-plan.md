# Worker Lifecycle Reference Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local worker process supervisor protocol and subprocess reference adapter with JSON-safe lifecycle evidence.

**Architecture:** Keep the implementation in `agentos.deployment`, not in runtime loops. Expose `WorkerProcessSpec`, `WorkerProcessState`, `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor`; document that the adapter is argv-only, redacts environment values, and does not own restart policy, autoscaling, secrets, Kubernetes, or systemd.

**Tech Stack:** Python dataclasses, `subprocess.Popen`, pytest, existing docs/readiness/public API tests.

---

### Task 1: Worker Process Tests

**Files:**
- Create: `tests/deployment/test_worker_process_supervisor.py`

- [x] Write failing tests for spec validation, JSON-safe evidence,
  subprocess exit evidence, explicit stop evidence, and duplicate running
  worker rejection.
- [x] Run `uv run pytest tests\deployment\test_worker_process_supervisor.py -q`
  and confirm the tests fail before implementation.

### Task 2: Deployment Boundary Implementation

**Files:**
- Modify: `src/agentos/deployment.py`

- [x] Add `WorkerProcessSpec`, `WorkerProcessState`,
  `WorkerProcessSupervisor`, and `LocalSubprocessWorkerSupervisor`.
- [x] Use tuple argv only and `shell=False`.
- [x] Expose `env_keys` only in evidence.
- [x] Run `uv run pytest tests\deployment\test_worker_process_supervisor.py -q`
  and confirm it passes.

### Task 3: Public API

**Files:**
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [x] Export worker process supervisor primitives from top-level `agentos`.
- [x] Assert `agentos.deployment` and top-level exports.

### Task 4: Docs And Skill Guidance

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/architecture.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [x] Document `LocalSubprocessWorkerSupervisor` as a reference adapter.
- [x] Keep production process supervision, restart, scaling, secrets, and live
  backend checks deployment-owned.

### Task 5: Verification

- [ ] Run targeted deployment/public/docs tests.
- [ ] Run full test suite.
- [ ] Run compileall.
- [ ] Scan `QueryLoop` and `AsyncQueryLoop` for leaked worker supervisor
  concepts.
- [ ] Run `git diff --check`.
