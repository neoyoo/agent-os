# Nacos Registry Adapter Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Nacos registry/discovery adapter boundary for AgentCard, endpoint, capabilities, version, worker service metadata, and health metadata without using Nacos as task, plan, session, queue, or worker runtime truth.

**Architecture:** Add a client protocol and adapter/resolver in `agentos.registry.nacos`. The SDK maps `AgentCard` values into JSON-safe Nacos instance metadata and maps healthy Nacos instances back into `AgentCard` values through an injected client. Unit tests use a fake client; deployments own the real Nacos client, credentials, TLS, tenant policy, and live backend verification.

**Tech Stack:** Python dataclasses, protocols, JSON serialization, pytest, existing registry/public API/readiness/docs tests.

---

## File Structure

- Create `src/agentos/registry/nacos.py`: Nacos protocol, config, evidence, adapter, resolver, metadata helpers, and errors.
- Modify `src/agentos/registry/__init__.py`: export Nacos registry primitives.
- Modify `src/agentos/__init__.py`: export top-level Nacos registry primitives.
- Add `tests/registry/test_nacos_registry_adapter.py`: TDD behavior tests with a fake client.
- Modify `tests/architecture/test_public_api.py`: assert public exports.
- Modify `tests/docs/test_production_readiness_docs.py` and `tests/docs/test_objective_coverage_audit_docs.py`: assert docs/skill guidance.
- Modify `docs/production-readiness.md`, `docs/agentos-objective-coverage-audit.md`, `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`, and `.claude/skills/agent-os` guidance.

## Task 1: RED Registry Adapter Tests

- [ ] Add fake-client tests proving `NacosAgentRegistryAdapter.register(...)` projects an `AgentCard` into Nacos service metadata with endpoint, capabilities, version, health, worker service metadata, and no truth-state fields.
- [ ] Add tests proving `NacosAgentCardResolver.resolve(...)` and `discover(...)` return healthy cards and filter capabilities.
- [ ] Add tests proving invalid metadata raises `NacosRegistryError`.
- [ ] Add tests proving evidence is JSON-safe and excludes credentials/env values.
- [ ] Run:

```powershell
uv run pytest tests\registry\test_nacos_registry_adapter.py -q
```

Expected: fail because the new Nacos registry primitives are not defined.

## Task 2: GREEN Adapter Implementation

- [ ] Implement `NacosRegistryClient`, `NacosRegistryConfig`, `NacosRegistryEvidence`, `NacosRegistryError`, `NacosAgentRegistryAdapter`, and `NacosAgentCardResolver`.
- [ ] Keep all operations delegated to the injected client.
- [ ] Keep metadata JSON-safe and discovery-only.
- [ ] Run:

```powershell
uv run pytest tests\registry\test_nacos_registry_adapter.py -q
```

Expected: pass.

## Task 3: Public API

- [ ] Export the new primitives from `agentos.registry` and top-level `agentos`.
- [ ] Add public API assertions.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py::test_remote_registry_and_channel_public_api_exports -q
```

Expected: pass.

## Task 4: Docs, Readiness, And Skill Guidance

- [ ] Update production readiness docs to describe the Nacos discovery boundary.
- [ ] Update objective coverage audit from 94% to 95% only if tests and docs prove the adapter boundary is implemented.
- [ ] Update roadmap with Phase 89 artifacts and conclusion.
- [ ] Update skill guidance so specs can choose `NacosAgentRegistryAdapter` as a registry backend while stating it is discovery-only.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\registry\test_nacos_registry_adapter.py tests\registry\test_remote_registry.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
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
rg -n "Nacos|NacosAgentRegistry|NacosAgentCardResolver" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] Diff hygiene:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if the command exits 0.
