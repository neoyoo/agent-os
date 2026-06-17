# Production Readiness Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 7 production readiness matrix so agent-os can describe supported agent forms with production-grade evidence and gaps.

**Architecture:** Add a static `agentos.readiness` module containing dataclasses and immutable form records. Keep it independent of runtime loops and deployment adapters. Update skill docs to reference the matrix as the shared production planning source of truth.

**Tech Stack:** Python 3.11 dataclasses, Literal types, tuples, pytest.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-production-readiness-matrix-design.md`.

Target conclusion:

```text
agent-os agent-form support is production-readable only when each form has
dimension-level evidence and explicit gaps.
```

Deferred:

- Live backend readiness probes.
- Automatic deployment/scaffold generation.
- Completing all production gaps.
- Runtime loop integration.

## File Structure

Modify/create:

- `src/agentos/readiness.py`
  Add dataclasses, levels, dimensions, and static form records.

- `src/agentos/__init__.py`
  Export readiness types and lookup functions.

- `tests/test_readiness.py`
  New tests for matrix shape, key form classifications, and red-flag gaps.

- `tests/architecture/test_public_api.py`
  Public API assertions.

- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/architecture.md`
- `.claude/skills/agent-os/flow/01-requirements.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
  Mention readiness matrix and production dimensions.

- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- `docs/plans/2026-06-11-agentscope2-a2a-parity-review.md`
  Add Phase 7 readiness-matrix acceptance evidence.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Matrix Shape

**Files:**
- Create: `tests/test_readiness.py`
- Create: `src/agentos/readiness.py`

- [x] Write failing tests that `list_agent_form_readiness()` returns the six
  initial forms and every form has all required dimensions.
- [x] Run the tests and confirm import/function failure.
- [x] Implement `ReadinessDimension`, `AgentFormReadiness`, lookup functions,
  and six form records.
- [x] Run `uv run pytest tests/test_readiness.py -q`.

## Task 2: Production Classification Checks

**Files:**
- Modify: `tests/test_readiness.py`
- Modify: `src/agentos/readiness.py`

- [x] Write failing tests that terminal is `direct`, web distributed is
  `primitives-ready`, and A2A/team/planner gaps are explicit.
- [x] Implement or adjust records until classifications and gap text pass.
- [x] Run `uv run pytest tests/test_readiness.py -q`.

## Task 3: Public API

**Files:**
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [x] Write failing public API assertions for readiness types and functions.
- [x] Export names from top-level `agentos`.
- [x] Run `uv run pytest tests/architecture/test_public_api.py -q`.

## Task 4: Skill And Review Docs

**Files:**
- Modify listed `.claude/skills/agent-os` docs.
- Modify listed `docs/plans` review docs.

- [x] Update docs to reference `agentos.readiness`.
- [x] Update requirement/spec-generation guidance to include production
  dimensions for production deployments.
- [x] Run docs search for stale wording that says production-ready without
  dimension evidence.

## Task 5: Verification

- [x] Run `uv run pytest tests/test_readiness.py tests/architecture/test_public_api.py -q`.
- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Run runtime boundary search:

```powershell
rg "agentos.readiness|ReadinessLevel|AgentFormReadiness" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

## Self-Review

- Spec coverage: matrix shape, six forms, classifications, public API, docs,
  and verification are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: no runtime loop changes.
