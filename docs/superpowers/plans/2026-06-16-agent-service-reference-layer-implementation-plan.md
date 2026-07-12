# Agent Service Reference Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight Agent Service reference layer that composes existing web runtime, ASGI, session, workspace, auth/rate-limit, and readiness boundaries without owning deployment infrastructure.

**Architecture:** Create `agentos.service` as a focused composition module. `AgentServiceReferenceProfile` reports service-level readiness, while `AgentServiceReference` builds an `AsgiAgentApp` from an injected runtime profile and aggregates child readiness checks. No new router, concrete backend credentials, or query-loop changes are introduced.

**Tech Stack:** Python dataclasses/protocol-like duck typing, existing `AsgiAgentApp`, `DistributedWebRuntimeProfile`, readiness profile classes, pytest.

---

## File Structure

- Create `src/agentos/service.py`: reference service profile, composition object, JSON-safe helpers, and public exports.
- Modify `src/agentos/__init__.py`: top-level exports for the new service primitives.
- Add `tests/service/test_agent_service_reference.py`: TDD tests for composition, readiness, auth/rate-limit injection, and JSON-safe evidence.
- Modify `tests/architecture/test_public_api.py`: public API assertions.
- Modify docs/readiness tests and docs/skill guidance for Phase 90 coverage.

## Task 1: RED Service Reference Tests

- [ ] Add tests showing `AgentServiceReference` builds an `AsgiAgentApp` from a `DistributedWebRuntimeProfile`.
- [ ] Add tests showing `/ready` includes the service readiness check and returns not-ready while deployment-owned components are missing.
- [ ] Add tests showing injected `ChannelAuthPolicy` and `RateLimiter` are enforced by the built app.
- [ ] Add tests showing readiness metadata is JSON-safe and excludes credential values.
- [ ] Run:

```powershell
uv run pytest tests\service\test_agent_service_reference.py -q
```

Expected: fail because `agentos.service` does not exist.

## Task 2: GREEN Service Reference Implementation

- [ ] Implement `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS`.
- [ ] Implement `AgentServiceReferenceProfile` with `missing_components()`, `readiness_metadata()`, and `readiness_check()`.
- [ ] Implement `AgentServiceReference.build_asgi_app()` by calling `AsgiAgentApp` with the injected profile session provider, auth policy, rate limiter, and aggregated readiness checks.
- [ ] Keep metadata JSON-safe and never include credential values.
- [ ] Run:

```powershell
uv run pytest tests\service\test_agent_service_reference.py -q
```

Expected: pass.

## Task 3: Public API

- [ ] Export service primitives from top-level `agentos`.
- [ ] Add public API assertions for `agentos.service` and top-level `agentos`.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py::test_public_api_uses_responsibility_specific_names -q
```

Expected: pass.

## Task 4: Docs, Readiness, And Skill Guidance

- [ ] Update production readiness docs with an Agent Service Reference Layer section.
- [ ] Update objective coverage audit to include Phase 90 evidence and keep the long-running goal active.
- [ ] Update roadmap with Phase 90 target conclusion, artifacts, and conclusion.
- [ ] Update `.claude/skills/agent-os` guidance so web agent specs can choose the reference service layer while naming deployment-owned responsibilities.
- [ ] Add docs tests that require the new terms in production docs, audit, roadmap, and skill guidance.
- [ ] Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\service\test_agent_service_reference.py tests\runtime\test_runtime_profile.py tests\channels\test_health_endpoint.py tests\architecture\test_public_api.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

- [ ] Run full tests:

```powershell
uv run pytest -q
```

- [ ] Compile:

```powershell
uv run python -m compileall -q src tests
```

- [ ] Boundary scan:

```powershell
rg -n "AgentServiceReference|AgentServiceReferenceProfile|agentos.service" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] Diff hygiene:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if the command exits 0.
