# Distributed Web Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `DistributedWebRuntimeProfile` as a production-oriented profile preset for web agents with durable distributed session hydration.

**Architecture:** Build on existing profile and channel boundaries. The preset owns assembly of `DurableAgentSessionProvider`, distributed lease store, snapshot persistence, workspace metadata, and ASGI channel app construction while leaving `QueryLoop`/`AsyncQueryLoop` untouched.

**Tech Stack:** Python 3.11 dataclasses, protocols, pytest, existing agent-os channel/profile primitives.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-distributed-web-profile-design.md`.

Target conclusion:

```text
Distributed web agents need an SDK profile preset that assembles durable
session, distributed lease, snapshot persistence, workspace, and channel policy;
raw adapters alone still leave production users wiring too much by hand.
```

Deferred:

- live Redis/Postgres integration tests
- migration runner
- secret/credential management
- new ASGI routes
- runtime loop changes

## File Structure

Modify:

- `src/agentos/runtime/profile.py`
  Add `DistributedWebRuntimeProfile`.

- `src/agentos/runtime/__init__.py`
  Export `DistributedWebRuntimeProfile`.

- `src/agentos/__init__.py`
  Export `DistributedWebRuntimeProfile`.

- `src/agentos/readiness.py`
  Update web distributed recommended profile and evidence.

- `.claude/skills/agent-os/modules/architecture.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/persistence.md`
- `.claude/skills/agent-os/flow/01-requirements.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `docs/production-readiness.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
  Document the profile preset.

Modify tests:

- `tests/runtime/test_runtime_profile.py`
- `tests/architecture/test_public_api.py`
- `tests/test_readiness.py`

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Profile Assembly

**Files:**
- Modify: `tests/runtime/test_runtime_profile.py`
- Modify: `src/agentos/runtime/profile.py`

- [x] **Step 1: Write failing profile assembly tests**

Tests should cover:

- `DistributedWebRuntimeProfile` builds a `DurableAgentSessionProvider`.
- two profile instances sharing fake lease/persistence can hydrate the same
  session across nodes
- `build_channel_app()` returns `AsgiAgentApp`
- `readiness_metadata()` exposes provider, lease store, persistence adapter,
  lifecycle, and production gaps

- [x] **Step 2: Run profile tests and verify failure**

Run:

```powershell
uv run pytest tests/runtime/test_runtime_profile.py::test_distributed_web_runtime_profile_assembles_durable_session_provider tests/runtime/test_runtime_profile.py::test_distributed_web_runtime_profile_hydrates_session_across_nodes tests/runtime/test_runtime_profile.py::test_distributed_web_runtime_profile_readiness_metadata_names_adapters -q
```

Expected: FAIL because `DistributedWebRuntimeProfile` is not defined.

- [x] **Step 3: Implement minimal profile preset**

Add the dataclass to `runtime/profile.py`. Reuse `DurableAgentSessionProvider`
and `AsgiAgentApp`; do not duplicate channel behavior.

- [x] **Step 4: Run profile tests and verify pass**

Run:

```powershell
uv run pytest tests/runtime/test_runtime_profile.py -q
```

Expected: PASS.

## Task 2: Public API And Readiness

**Files:**
- Modify: `src/agentos/runtime/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`

- [x] **Step 1: Write failing public API/readiness assertions**

Assert runtime and top-level exports include `DistributedWebRuntimeProfile`.
Assert web distributed readiness recommends this profile and evidence includes
it.

- [x] **Step 2: Run public/readiness tests and verify failure**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py::test_public_api_uses_responsibility_specific_names tests/test_readiness.py::test_web_distributed_session_names_lease_and_snapshot_gap -q
```

Expected: FAIL before exports/readiness updates.

- [x] **Step 3: Export and update readiness**

Export the profile and update web distributed readiness metadata.

- [x] **Step 4: Run public/readiness tests and verify pass**

Run:

```powershell
uv run pytest tests/architecture/test_public_api.py tests/test_readiness.py -q
```

Expected: PASS.

## Task 3: Docs And Skill Guidance

**Files:**
- Modify docs listed in File Structure.

- [x] **Step 1: Update docs**

Explain that web distributed specs should use `DistributedWebRuntimeProfile`
when they use durable Redis/Postgres-backed session hydration.

- [x] **Step 2: Run docs tests**

Run:

```powershell
uv run pytest tests/docs/test_production_readiness_docs.py -q
```

Expected: PASS.

## Task 4: Verification

- [x] Run focused tests:

```powershell
uv run pytest tests/runtime/test_runtime_profile.py tests/channels/test_durable_session_provider.py tests/channels/test_redis_session_lease_store.py tests/persistence/test_postgres_session_snapshot_persistence.py -q
```

- [x] Run runtime boundary search:

```powershell
rg "DistributedWebRuntimeProfile|RedisSessionLeaseStore|PostgresSessionSnapshotPersistence|DurableAgentSessionProvider|agentos.channels|agentos.persistence" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: profile assembly, cross-node hydration, ASGI app construction,
  readiness metadata, exports, docs, and runtime boundary are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: runtime loops remain deployment-agnostic.
