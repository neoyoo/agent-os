# Sandbox / Workspace Backend Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable workspace execution backend boundary and a local reference adapter without implementing Docker/E2B sandbox kernels in SDK core.

**Architecture:** Add request/result/policy/protocol dataclasses to `agentos.workspace` beside existing workspace primitives. The local backend executes argv tuples with `subprocess.run(shell=False)`, enforces cwd inside the workspace root before execution, captures stdout/stderr for the caller, and emits JSON-safe audit evidence that redacts environment values. Docker, E2B, network policy, resource enforcement, image patching, and live backend verification remain deployment-owned adapter work.

**Tech Stack:** Python dataclasses, protocols, pathlib, subprocess, asyncio.to_thread, pytest, existing docs/readiness/public API tests.

---

## File Structure

- Modify `src/agentos/workspace.py`: add execution request/result/policy/protocol/local backend.
- Modify `src/agentos/__init__.py`: export new workspace execution primitives.
- Modify `tests/test_workspace.py`: add TDD behavior tests.
- Modify `tests/architecture/test_public_api.py`: assert public API exports.
- Modify `src/agentos/readiness.py`: add evidence for workspace backend boundary.
- Modify `docs/production-readiness.md`, `docs/agentos-objective-coverage-audit.md`, and `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`.
- Modify `.claude/skills/agent-os/modules/architecture.md`, `.claude/skills/agent-os/modules/agent-forms.md`, `.claude/skills/agent-os/modules/multi-agent.md`, and `.claude/skills/agent-os/flow/02-spec-generation.md`.

## Task 1: RED Workspace Backend Tests

- [ ] Add tests for argv validation, local execution success, cwd escape rejection, capability allow-list rejection, timeout evidence, async execution, and env value redaction.
- [ ] Run:

```powershell
uv run pytest tests\test_workspace.py -q
```

Expected: fails because the new workspace execution primitives are not defined.

## Task 2: GREEN Workspace Backend Implementation

- [ ] Implement `WorkspaceExecutionRequest`, `WorkspaceExecutionResult`, `WorkspaceExecutionPolicy`, `WorkspaceExecutionBackend`, `SandboxBackend`, `LocalWorkspaceExecutionBackend`, and `WorkspaceExecutionError`.
- [ ] Run:

```powershell
uv run pytest tests\test_workspace.py -q
```

Expected: workspace tests pass.

## Task 3: Public API

- [ ] Export the new primitives from top-level `agentos`.
- [ ] Add public API assertions.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py::test_workspace_public_api_exports -q
```

Expected: public API test passes.

## Task 4: Docs, Readiness, And Skill Guidance

- [ ] Update readiness evidence and docs to describe the local reference backend.
- [ ] Update skill guidance so specs must choose a sandbox/workspace backend.
- [ ] Update roadmap and objective coverage audit.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: docs/readiness tests pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\test_workspace.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
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
rg -n "WorkspaceExecutionBackend|LocalWorkspaceExecutionBackend|SandboxBackend|subprocess|run\\(" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] Diff hygiene:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if the command exits 0.
