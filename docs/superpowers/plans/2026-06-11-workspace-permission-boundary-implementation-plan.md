# Workspace Permission Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 3A workspace and permission boundary primitives so terminal, web, and future team agents can refer to explicit execution workspaces.

**Architecture:** Add a focused `agentos.workspace` module with dataclasses, protocols, local provider, and policy checks. Runtime profiles may carry workspace metadata, but `QueryLoop` remains unaware. This phase introduces the vocabulary and tests; it does not implement OS sandboxing or tool backend path rewriting.

**Tech Stack:** Python 3.11 dataclasses/protocols/pathlib, existing runtime profile module, pytest.

---

## Scope Contract

This plan implements only Phase 3A from `docs/superpowers/specs/2026-06-11-workspace-permission-boundary-design.md`.

Target conclusion:

```text
Workspace is an execution boundary for a session/profile/team, not an implementation detail of individual tools.
Terminal agents may default to local cwd, but web and distributed agents must receive an explicit workspace handle.
Subagents and team workers may only receive the same or narrower workspace and permissions than their parent.
```

Deferred:

- ToolExecutor/ExecutionBackend workspace-aware path enforcement.
- Container or OS sandboxing.
- Remote workspace service.
- Durable session provider workspace binding.
- A2A card workspace declarations.
- Team tools and team runtime.

## File Structure

Create:

- `src/agentos/workspace.py`  
  Owns workspace dataclasses, provider protocol, local provider, and policy checks.

- `tests/test_workspace.py`  
  Covers local provider resolution and policy narrowing.

Modify:

- `src/agentos/__init__.py`  
  Export public workspace primitives.

- `tests/architecture/test_public_api.py`  
  Assert public API and runtime import boundary.

- `src/agentos/runtime/profile.py`  
  Add optional workspace metadata to `LocalRuntimeProfile` and `WebRuntimeProfile` if the current profile dataclasses make this small.

- `tests/runtime/test_runtime_profile.py`  
  Add tests for profile workspace metadata if profile code is modified.

- `.claude/skills/agent-os/modules/architecture.md`  
  Document workspace as profile/session boundary.

- `.claude/skills/agent-os/modules/agent-forms.md`  
  Update workspace readiness wording.

- `.claude/skills/agent-os/modules/multi-agent.md`  
  Document subagent workspace narrowing requirement.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/capabilities/executor.py` in Phase 3A unless a test proves a tiny type import is needed

## Task 1: Add Workspace Primitives

**Files:**
- Create: `src/agentos/workspace.py`
- Test: `tests/test_workspace.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_workspace.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.workspace import (
    LocalWorkspaceProvider,
    WorkspacePolicy,
    WorkspacePolicyError,
    WorkspaceRequest,
)


def test_local_workspace_provider_resolves_process_workspace(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)

    handle = provider.resolve_workspace(
        WorkspaceRequest(agent_id="agent_a", requested_scope="process"),
    )

    assert handle.workspace_id == "process:agent_a"
    assert handle.scope == "process"
    assert handle.root == str(tmp_path)


def test_local_workspace_provider_resolves_session_workspace(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)

    handle = provider.resolve_workspace(
        WorkspaceRequest(
            agent_id="agent_a",
            user_id="user_1",
            session_id="session_1",
            requested_scope="session",
        ),
    )

    assert handle.workspace_id == "session:session_1"
    assert handle.scope == "session"
    assert handle.root == str(tmp_path / "sessions" / "session_1")
    assert handle.metadata == {
        "agent_id": "agent_a",
        "user_id": "user_1",
        "session_id": "session_1",
    }


def test_workspace_policy_allows_narrowing_but_rejects_broadening(
    tmp_path: Path,
) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)
    policy = WorkspacePolicy()
    parent = provider.resolve_workspace(
        WorkspaceRequest(session_id="session_1", requested_scope="session"),
    )

    child = provider.narrow_workspace(parent, child_id="task_1", scope="task")

    assert child.scope == "task"
    assert child.parent_workspace_id == parent.workspace_id
    policy.ensure_child_workspace_allowed(parent, child)

    wider = provider.narrow_workspace(child, child_id="session_2", scope="session")
    with pytest.raises(WorkspacePolicyError, match="cannot broaden workspace"):
        policy.ensure_child_workspace_allowed(child, wider)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_workspace.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.workspace'`.

- [ ] **Step 3: Implement workspace module**

Create `src/agentos/workspace.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Protocol


WorkspaceScope = Literal["process", "agent", "user", "session", "team", "task"]

_SCOPE_RANK: dict[WorkspaceScope, int] = {
    "process": 5,
    "user": 4,
    "agent": 3,
    "team": 2,
    "session": 1,
    "task": 0,
}


class WorkspacePolicyError(PermissionError):
    """Raised when workspace policy rejects a request."""


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    """Stable reference to an execution workspace."""

    workspace_id: str
    scope: WorkspaceScope
    root: str | None = None
    parent_workspace_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkspaceRequest:
    """Inputs used to resolve a workspace for an agent/session/task."""

    agent_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    team_id: str | None = None
    task_id: str | None = None
    requested_scope: WorkspaceScope = "session"


class WorkspaceProvider(Protocol):
    """Resolves execution workspaces for profiles and tasks."""

    def resolve_workspace(self, request: WorkspaceRequest) -> WorkspaceHandle:
        """Return the workspace handle for a profile/session/task."""

    def narrow_workspace(
        self,
        parent: WorkspaceHandle,
        *,
        child_id: str,
        scope: WorkspaceScope = "task",
    ) -> WorkspaceHandle:
        """Return a child workspace that cannot broaden parent access."""


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    """Policy for allowed workspace scopes and child narrowing."""

    allow_parent_access: bool = False
    allowed_scopes: frozenset[WorkspaceScope] = frozenset({"session", "task"})
    require_explicit_web_workspace: bool = True

    def ensure_scope_allowed(self, scope: WorkspaceScope) -> None:
        if scope not in self.allowed_scopes:
            raise WorkspacePolicyError(f"workspace scope not allowed: {scope}")

    def ensure_child_workspace_allowed(
        self,
        parent: WorkspaceHandle,
        child: WorkspaceHandle,
    ) -> None:
        if _SCOPE_RANK[child.scope] > _SCOPE_RANK[parent.scope]:
            raise WorkspacePolicyError(
                f"cannot broaden workspace from {parent.scope} to {child.scope}",
            )
        self.ensure_scope_allowed(child.scope)


@dataclass(slots=True)
class LocalWorkspaceProvider:
    """Workspace provider for local development and deterministic tests."""

    base_dir: Path | str | None = None
    create: bool = False

    def resolve_workspace(self, request: WorkspaceRequest) -> WorkspaceHandle:
        scope = request.requested_scope
        workspace_id = self._workspace_id(scope, request)
        root = self._root_for(scope, workspace_id)
        metadata = {
            key: value
            for key, value in {
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "session_id": request.session_id,
                "team_id": request.team_id,
                "task_id": request.task_id,
            }.items()
            if value is not None
        }
        return WorkspaceHandle(
            workspace_id=workspace_id,
            scope=scope,
            root=str(root) if root is not None else None,
            metadata=metadata,
        )

    def narrow_workspace(
        self,
        parent: WorkspaceHandle,
        *,
        child_id: str,
        scope: WorkspaceScope = "task",
    ) -> WorkspaceHandle:
        root = None
        if parent.root is not None:
            root = Path(parent.root) / f"{scope}s" / child_id
            if self.create:
                root.mkdir(parents=True, exist_ok=True)
        return WorkspaceHandle(
            workspace_id=f"{scope}:{child_id}",
            scope=scope,
            root=str(root) if root is not None else None,
            parent_workspace_id=parent.workspace_id,
            metadata={"child_id": child_id},
        )

    def _workspace_id(
        self,
        scope: WorkspaceScope,
        request: WorkspaceRequest,
    ) -> str:
        value_by_scope = {
            "process": request.agent_id or "default",
            "agent": request.agent_id,
            "user": request.user_id,
            "session": request.session_id,
            "team": request.team_id,
            "task": request.task_id,
        }
        value = value_by_scope[scope]
        if value is None:
            raise WorkspacePolicyError(f"{scope}_id is required")
        return f"{scope}:{value}"

    def _root_for(self, scope: WorkspaceScope, workspace_id: str) -> Path | None:
        base = Path.cwd() if self.base_dir is None else Path(self.base_dir)
        if scope == "process":
            root = base
        else:
            _prefix, value = workspace_id.split(":", 1)
            root = base / f"{scope}s" / value
        if self.create:
            root.mkdir(parents=True, exist_ok=True)
        return root
```

- [ ] **Step 4: Run workspace tests**

Run:

```bash
uv run pytest tests/test_workspace.py -q
```

Expected: PASS.

## Task 2: Public API Exports

**Files:**
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add failing public API tests**

In `tests/architecture/test_public_api.py`, add a test:

```python
def test_workspace_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    workspace = importlib.import_module("agentos.workspace")

    for name in [
        "LocalWorkspaceProvider",
        "WorkspaceHandle",
        "WorkspacePolicy",
        "WorkspacePolicyError",
        "WorkspaceProvider",
        "WorkspaceRequest",
        "WorkspaceScope",
    ]:
        assert hasattr(workspace, name)
        assert hasattr(agentos, name)
```

- [ ] **Step 2: Run public API test to verify failure**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_workspace_public_api_exports -q
```

Expected: FAIL because top-level `agentos` does not export the names.

- [ ] **Step 3: Export names**

Modify `src/agentos/__init__.py`:

```python
from agentos.workspace import (
    LocalWorkspaceProvider,
    WorkspaceHandle,
    WorkspacePolicy,
    WorkspacePolicyError,
    WorkspaceProvider,
    WorkspaceRequest,
    WorkspaceScope,
)
```

Add the same names to `__all__`.

- [ ] **Step 4: Run public API tests**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py -q
```

Expected: PASS.

## Task 3: RuntimeProfile Workspace Metadata

**Files:**
- Modify: `src/agentos/runtime/profile.py`
- Modify: `tests/runtime/test_runtime_profile.py`

- [ ] **Step 1: Add failing profile tests**

Append to `tests/runtime/test_runtime_profile.py`:

```python
from agentos.workspace import LocalWorkspaceProvider, WorkspaceRequest


def test_local_runtime_profile_resolves_process_workspace(tmp_path) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)
    profile = LocalRuntimeProfile(
        agent_builder=builder_with_response("ok"),
        workspace_provider=provider,
        workspace_request=WorkspaceRequest(
            agent_id="agent_a",
            requested_scope="process",
        ),
    )

    assert profile.workspace_handle is not None
    assert profile.workspace_handle.scope == "process"
    assert profile.workspace_handle.root == str(tmp_path)


def test_web_runtime_profile_exposes_explicit_workspace(tmp_path) -> None:
    session_provider = InMemoryAgentSessionProvider(
        lambda session_id: builder_with_response("ok").build(),
    )
    provider = LocalWorkspaceProvider(base_dir=tmp_path)
    profile = WebRuntimeProfile(
        session_provider=session_provider,
        workspace_provider=provider,
        workspace_request=WorkspaceRequest(
            session_id="session_1",
            requested_scope="session",
        ),
    )

    assert profile.workspace_handle is not None
    assert profile.workspace_handle.workspace_id == "session:session_1"
```

Use existing helper names from the file. If the helper is named differently, adapt only the test setup to the existing helper.

- [ ] **Step 2: Run profile tests to verify failure**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py::test_local_runtime_profile_resolves_process_workspace tests/runtime/test_runtime_profile.py::test_web_runtime_profile_exposes_explicit_workspace -q
```

Expected: FAIL because profile constructors do not accept workspace arguments.

- [ ] **Step 3: Implement metadata**

In `src/agentos/runtime/profile.py`, import under `TYPE_CHECKING` if needed:

```python
from agentos.workspace import WorkspaceHandle, WorkspaceProvider, WorkspaceRequest
```

Add fields to `LocalRuntimeProfile` and `WebRuntimeProfile`:

```python
workspace_provider: WorkspaceProvider | None = None
workspace_request: WorkspaceRequest | None = None
workspace_handle: WorkspaceHandle | None = field(init=False, default=None)

def __post_init__(self) -> None:
    if self.workspace_provider is not None:
        request = self.workspace_request or WorkspaceRequest(
            requested_scope="process",
        )
        object.__setattr__(
            self,
            "workspace_handle",
            self.workspace_provider.resolve_workspace(request),
        )
```

If existing dataclasses are not frozen, use direct assignment instead of `object.__setattr__`.

For `WebRuntimeProfile`, default `workspace_request` to:

```python
WorkspaceRequest(session_id=self.session_id, requested_scope="session")
```

only when a `workspace_provider` is supplied.

- [ ] **Step 4: Run runtime profile tests**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py -q
```

Expected: PASS.

## Task 4: Skill Documentation Alignment

**Files:**
- Modify: `.claude/skills/agent-os/modules/architecture.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`

- [ ] **Step 1: Update architecture docs**

In `architecture.md`, update the production shape boundary:

```markdown
- Workspace primitives are direct: `WorkspaceHandle`, `WorkspaceProvider`, `WorkspacePolicy`, and `LocalWorkspaceProvider`.
- Production web workspace enforcement is primitives-ready: profiles can carry workspace metadata, but tool backends and sandbox enforcement remain later work.
```

Update the extension table:

```markdown
| Execution workspace boundary | `WorkspaceProvider` + `WorkspacePolicy`; terminal may default to local cwd, web should pass explicit workspace |
```

- [ ] **Step 2: Update agent forms docs**

In `Workspace-Isolated Production Agent`, change gap to:

```markdown
Workspace primitives and policy are available; tool backend enforcement, sandboxing, and team workspace runtime remain future work.
```

In Production Readiness Notes, add:

```markdown
- Workspace handles are now SDK primitives. They do not by themselves enforce filesystem or process sandboxing.
```

- [ ] **Step 3: Update multi-agent docs**

In Team Discussion Gap, add:

```markdown
Worker sessions should receive narrowed `WorkspaceHandle` values. Team runtime must pass artifact/evidence handles explicitly rather than sharing parent active messages or broad filesystem roots.
```

- [ ] **Step 4: Run drift search**

Run:

```bash
rg "workspace.*not yet|workspace is not yet|hard session/profile boundary|process cwd.*web|WorkspaceHandle" .claude docs src tests
```

Expected: no stale claim that workspace primitives are absent; warnings about sandbox/runtime enforcement are acceptable.

## Task 5: Verification

**Files:** all changed files

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/test_workspace.py tests/runtime/test_runtime_profile.py tests/architecture/test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compileall**

Run:

```bash
uv run python -m compileall -q src tests
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run final diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. Windows line-ending warnings are acceptable if no error lines are printed.

- [ ] **Step 5: Boundary search**

Run:

```bash
rg "agentos.channels|agentos.persistence|agentos.workspace|runtime.profile|Redis|Postgres" src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py
```

Expected: no matches.

## Self-Review

Spec coverage:

- Workspace primitives: Tasks 1 and 2.
- Terminal default: Tasks 1 and 3.
- Web explicit workspace metadata: Task 3.
- Subagent narrowing: Task 1.
- Docs alignment: Task 4.
- QueryLoop agnostic invariant: Task 5.

Placeholder scan:

- No implementation step contains red-flag placeholders or unspecified tests.

Type consistency:

- Names match the Phase 3 design spec: `WorkspaceScope`, `WorkspaceHandle`, `WorkspaceRequest`, `WorkspaceProvider`, `WorkspacePolicy`, `WorkspacePolicyError`, `LocalWorkspaceProvider`.
