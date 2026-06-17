# Workspace Security P1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the current workspace/security P1 review findings by making MCP execution authorization unforgeable by SDK consumers and redacting free-form secret patterns from workspace evidence metadata.

**Architecture:** MCP tool execution must remain routed through `ToolCallRouter`, where schema validation, security policy, sandbox policy, and capability checks happen before the MCP client is called. Replace the public boolean prevalidation flag with an internal validation marker object issued only by the adapter/router path, and apply shared redaction to every evidence string while preserving existing key-based full redaction.

**Tech Stack:** Python 3.11, pytest, agent-os SDK modules under `src/agentos/capabilities` and `src/agentos/workspace.py`.

---

### Task 1: Make MCP Prevalidation Unforgeable

**Files:**
- Modify: `src/agentos/capabilities/mcp.py`
- Modify: `src/agentos/capabilities/router.py`
- Test: `tests/capabilities/test_mcp.py`

- [ ] **Step 1: Write the failing direct-forgery test**

Add this test near `test_mcp_tool_adapter_direct_execute_requires_prevalidation_marker` in `tests/capabilities/test_mcp.py`:

```python
def test_mcp_tool_adapter_rejects_forged_public_prevalidation_flag() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    adapter = MCPToolAdapter(registry)

    with pytest.raises(ToolExecutionError, match="ToolCallRouter"):
        adapter.execute(
            ProviderToolCall(
                id="call_1",
                name="mcp__github__create_issue",
                arguments={"title": "Bug"},
            ),
            prevalidated=True,
        )

    assert client.calls == []
```

- [ ] **Step 2: Run the RED test**

Run:

```powershell
uv run pytest tests\capabilities\test_mcp.py::test_mcp_tool_adapter_rejects_forged_public_prevalidation_flag -q
```

Expected: FAIL because the current public `prevalidated=True` boolean reaches the fake MCP client.

- [ ] **Step 3: Implement an internal validation marker**

In `src/agentos/capabilities/mcp.py`, add a private frozen dataclass and an adapter-issued marker method:

```python
@dataclass(frozen=True, slots=True)
class _MCPPrevalidationMarker:
    adapter_id: int


@dataclass(slots=True)
class MCPToolAdapter:
    registry: MCPRegistry

    def prevalidation_marker(self) -> _MCPPrevalidationMarker:
        return _MCPPrevalidationMarker(adapter_id=id(self))
```

Change `MCPToolAdapter.execute` to accept the marker instead of a boolean:

```python
def execute(
    self,
    tool_call: ProviderToolCall,
    *,
    prevalidated: _MCPPrevalidationMarker | None = None,
) -> ToolExecutionResult:
    """Execute an MCP provider tool call after router validation."""

    if prevalidated != _MCPPrevalidationMarker(adapter_id=id(self)):
        raise ToolExecutionError(
            "MCPToolAdapter.execute() requires ToolCallRouter validation; "
            "route production MCP tool calls through ToolCallRouter",
        )
```

In `src/agentos/capabilities/router.py`, pass the marker after `_prepare_mcp_tool_call`:

```python
prepared_call = self._prepare_mcp_tool_call(tool_call)
return self.mcp_adapter.execute(
    prepared_call,
    prevalidated=self.mcp_adapter.prevalidation_marker(),
)
```

- [ ] **Step 4: Update direct adapter tests to use the router path**

Replace the body of `test_mcp_tool_adapter_executes_prefixed_provider_call` with a router call:

```python
router = ToolCallRouter(
    tool_registry=ToolRegistry(),
    mcp_adapter=adapter,
)

result = router.execute_tool_call(
    ProviderToolCall(
        id="call_1",
        name="mcp__github__create_issue",
        arguments={"title": "Bug"},
    ),
)
```

Replace the body of `test_mcp_registry_refreshes_automatically_after_register` the same way so normal execution evidence proves the public routed path, not direct boolean bypass.

- [ ] **Step 5: Run MCP focused tests**

Run:

```powershell
uv run pytest tests\capabilities\test_mcp.py -q
```

Expected: all MCP tests pass; invalid schema, sandbox escape, and capability-denied tests must still assert `client.calls == []`.

---

### Task 2: Redact Free-Form Workspace Evidence Metadata

**Files:**
- Modify: `src/agentos/workspace.py`
- Test: `tests/test_workspace.py`

- [ ] **Step 1: Write the failing metadata value redaction test**

Add this test after `test_workspace_execution_result_redacts_secret_like_metadata_in_evidence` in `tests/test_workspace.py`:

```python
def test_workspace_execution_result_redacts_secret_patterns_in_metadata_values(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=("python", "-c", "print('ok')"),
            capability="process.exec",
            metadata={
                "note": "Authorization: Bearer raw-token",
                "database": "postgres://user:raw-password@db/app",
                "nested": ["api_key=sk-live-secret", {"safe": "visible"}],
            },
        ),
    )

    evidence = result.to_evidence()

    assert evidence["metadata"]["note"] == "Authorization: Bearer <redacted>"
    assert evidence["metadata"]["database"] == "postgres://<redacted>@db/app"
    assert evidence["metadata"]["nested"][0] == "api_key=<redacted>"
    assert evidence["metadata"]["nested"][1]["safe"] == "visible"
    assert "raw-token" not in str(evidence)
    assert "raw-password" not in str(evidence)
    assert "sk-live-secret" not in str(evidence)
```

- [ ] **Step 2: Run the RED test**

Run:

```powershell
uv run pytest tests\test_workspace.py::test_workspace_execution_result_redacts_secret_patterns_in_metadata_values -q
```

Expected: FAIL because neutral metadata values currently pass through `_json_safe_value` unchanged.

- [ ] **Step 3: Use shared redaction for every evidence string**

In `src/agentos/workspace.py`, change the redaction import:

```python
from agentos._redaction import (
    is_secret_like_key,
    redact_command_argv,
    redact_secret_patterns,
)
```

Update `_json_safe_value` so secret-like keys still redact the whole value, but ordinary strings are pattern-redacted:

```python
def _json_safe_value(value: object, *, key_hint: str = "") -> object:
    if is_secret_like_key(key_hint):
        return "<redacted>"
    if isinstance(value, str):
        return redact_secret_patterns(value)
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return redact_secret_patterns(repr(value))
```

Remove the local `_is_secret_like_key` function and `_SECRET_LIKE_KEY_PARTS` constant from `src/agentos/workspace.py` after all callers use `is_secret_like_key`.

- [ ] **Step 4: Run workspace focused tests**

Run:

```powershell
uv run pytest tests\test_workspace.py -q
```

Expected: all workspace tests pass, including existing env-value, command-argument, and secret-key redaction tests.

---

### Task 3: Focused Security Gate And Commit

**Files:**
- Modify: `src/agentos/capabilities/mcp.py`
- Modify: `src/agentos/capabilities/router.py`
- Modify: `src/agentos/workspace.py`
- Modify: `tests/capabilities/test_mcp.py`
- Modify: `tests/test_workspace.py`

- [ ] **Step 1: Run combined focused gate**

Run:

```powershell
uv run pytest tests\capabilities\test_mcp.py tests\test_workspace.py -q
```

Expected: both security slices pass together.

- [ ] **Step 2: Run reviewer regression set**

Run:

```powershell
uv run pytest tests\test_workspace.py tests\capabilities\test_tool_sandbox_policy.py tests\capabilities\test_mcp.py tests\capabilities\test_execution_backend.py tests\deployment\test_worker_process_supervisor.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all selected tests pass. If a test fails, keep this task open and fix with a new RED test only when the failure reveals uncovered behavior.

- [ ] **Step 3: Run formatting diff check**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Existing CRLF warnings are acceptable only if the command still exits 0.

- [ ] **Step 4: Commit the Phase 1 fix**

Run:

```powershell
git add src\agentos\capabilities\mcp.py src\agentos\capabilities\router.py src\agentos\workspace.py tests\capabilities\test_mcp.py tests\test_workspace.py docs\superpowers\plans\2026-06-17-workspace-security-p1-hardening-implementation-plan.md
git commit -m "Harden workspace security evidence and MCP routing"
```

Expected: one commit on `review/agentos-sdk-architecture-20260611`; no branch switch.

---

### Task 4: Post-Fix Review Readiness Notes

**Files:**
- Inspect: `docs/production-readiness.md`
- Inspect: `docs/release-hardening.md`
- Inspect: `docs/public-api-inventory.json`

- [ ] **Step 1: Check whether docs already describe the new behavior**

Run:

```powershell
rg -n "MCP|prevalidation|ToolCallRouter|redact|metadata|workspace evidence" docs src\agentos
```

Expected: identify whether existing docs already say routed MCP calls and evidence redaction are SDK-owned boundaries.

- [ ] **Step 2: Only update docs if current claims become stale**

If docs claim `prevalidated=True` is a public usage path, replace that text with routed execution through `ToolCallRouter`. If docs already speak only at the policy-boundary level, do not add release-note noise.

- [ ] **Step 3: Keep release evidence pending**

Do not mark independent review as passed in `docs/release-evidence.json` during this phase. That gate stays pending until fresh neutral review passes after all blocking slices are addressed.

---

### Self-Review Checklist

- [ ] The public SDK caller cannot forge MCP validation with a boolean.
- [ ] Router-mediated MCP execution still works and still validates schema, sandbox, and capability policy before client calls.
- [ ] Workspace evidence redacts free-form secret patterns in neutral metadata values.
- [ ] Secret-like metadata keys still redact the entire value.
- [ ] Focused and reviewer regression tests pass.
- [ ] The commit stays on `review/agentos-sdk-architecture-20260611`.
