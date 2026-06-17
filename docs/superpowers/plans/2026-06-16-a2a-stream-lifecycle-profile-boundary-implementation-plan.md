# A2A Stream Lifecycle Profile Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-facing A2A stream lifecycle readiness profile without implementing stream lifecycle loops in SDK core.

**Architecture:** Add a small dataclass next to existing A2A deployment profiles. It returns JSON-safe readiness metadata and ASGI-compatible readiness checks that show which deployment-owned stream lifecycle components are configured. It does not call network transports, create workers, persist cursors, or touch runtime query loops.

**Tech Stack:** Python dataclasses, pytest, existing A2A channel exports and readiness docs.

---

## File Structure

- Modify `src/agentos/channels/a2a_operations.py`: add `A2AStreamLifecycleDeploymentProfile`.
- Modify `src/agentos/channels/__init__.py` and `src/agentos/__init__.py`: export the profile.
- Modify `tests/channels/test_a2a_operations.py`: add behavior tests.
- Modify `tests/architecture/test_public_api.py`: add public API assertions.
- Modify `src/agentos/readiness.py`: add readiness evidence.
- Modify `docs/production-readiness.md`: describe the lifecycle profile.
- Modify `.claude/skills/agent-os/modules/agent-forms.md` and `.claude/skills/agent-os/modules/multi-agent.md`: update SDK guidance.
- Modify `tests/test_readiness.py` and `tests/docs/test_production_readiness_docs.py`: lock docs/readiness.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 63.

## Task 1: RED Profile Behavior Tests

- [ ] Add `test_a2a_stream_lifecycle_profile_reports_missing_components` to `tests/channels/test_a2a_operations.py`:

```python
def test_a2a_stream_lifecycle_profile_reports_missing_components() -> None:
    from agentos.channels.a2a_operations import A2AStreamLifecycleDeploymentProfile

    profile = A2AStreamLifecycleDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "A2AStreamLifecycleDeploymentProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "reconnect_policy",
        "durable_cursor_store",
        "fanout_broker",
        "backpressure_policy",
        "stream_supervision",
    }
    assert "message/stream operation boundary" in metadata["sdk_owned"]
    assert "durable cursor storage" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False
```

- [ ] Add `test_a2a_stream_lifecycle_profile_marks_ready_when_components_are_configured`:

```python
def test_a2a_stream_lifecycle_profile_marks_ready_when_components_are_configured() -> None:
    from agentos.channels.a2a_operations import A2AStreamLifecycleDeploymentProfile

    profile = A2AStreamLifecycleDeploymentProfile(
        configured_components=(
            "reconnect_policy",
            "durable_cursor_store",
            "fanout_broker",
            "backpressure_policy",
            "stream_supervision",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True
```

- [ ] Add `test_a2a_stream_lifecycle_profile_rejects_empty_names`:

```python
with pytest.raises(ValueError, match="probe_name"):
    A2AStreamLifecycleDeploymentProfile(probe_name=" ")
with pytest.raises(ValueError, match="configured_components"):
    A2AStreamLifecycleDeploymentProfile(configured_components=("",))
```

- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: fails because `A2AStreamLifecycleDeploymentProfile` is not defined.

## Task 2: GREEN Profile Implementation

- [ ] Add constants near `A2APushNotificationDeploymentProfile`:

```python
A2A_STREAM_LIFECYCLE_REQUIRED_COMPONENTS = (
    "reconnect_policy",
    "durable_cursor_store",
    "fanout_broker",
    "backpressure_policy",
    "stream_supervision",
)
```

- [ ] Add `A2AStreamLifecycleDeploymentProfile` dataclass with:
  - `configured_components: tuple[str, ...] = ()`
  - `required_components: tuple[str, ...] = A2A_STREAM_LIFECYCLE_REQUIRED_COMPONENTS`
  - `probe_name: str = "a2a_stream_lifecycle"`
  - validation for non-empty probe and component names
  - `missing_components()`
  - `readiness_metadata()`
  - `readiness_check()`
- [ ] Ensure metadata returns tuples so it matches existing profile style.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: tests pass.

## Task 3: Public API

- [ ] Export `A2AStreamLifecycleDeploymentProfile` from `src/agentos/channels/__init__.py`.
- [ ] Export `A2AStreamLifecycleDeploymentProfile` from `src/agentos/__init__.py`.
- [ ] Add public API assertions in `tests/architecture/test_public_api.py`.
- [ ] Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 4: Docs And Readiness

- [ ] Add readiness evidence string `"A2AStreamLifecycleDeploymentProfile"`.
- [ ] Add assertions in `tests/test_readiness.py`.
- [ ] Update `docs/production-readiness.md` with profile usage and deployment ownership.
- [ ] Update `.claude/skills/agent-os/modules/agent-forms.md` and `.claude/skills/agent-os/modules/multi-agent.md`.
- [ ] Add doc assertions in `tests/docs/test_production_readiness_docs.py`.
- [ ] Append Phase 63 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all pass.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] Run full tests:

```powershell
uv run pytest -q
```

- [ ] Compile:

```powershell
uv run python -m compileall -q src tests
```

- [ ] Runtime boundary scan:

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] Diff hygiene:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if the command exits 0.
