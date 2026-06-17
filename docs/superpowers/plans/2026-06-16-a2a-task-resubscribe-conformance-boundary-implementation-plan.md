# A2A Task Resubscribe Conformance Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SDK self-conformance checks for the `tasks/resubscribe` JSON-RPC envelope and `statusUpdate` task subscription event shape.

**Architecture:** Extend `A2AConformanceHarness` with two narrow checks and optional caller-provided sample payloads. Reuse existing A2A operation serializers and task subscription event parser so the harness verifies the protocol surface already exposed by the server, client, ASGI route, and docs.

**Tech Stack:** Python, pytest, existing A2A channel serializers and conformance report model.

---

## File Structure

- Modify `src/agentos/channels/a2a_conformance.py`: add task resubscribe sample payloads, run parameters, and two conformance checks.
- Modify `tests/channels/test_a2a_conformance.py`: add RED tests for passing default checks and malformed request/event failures.
- Modify `src/agentos/readiness.py`: add readiness evidence for the new self-conformance checks.
- Modify `docs/production-readiness.md`: describe the task resubscribe self-conformance boundary.
- Modify `.claude/skills/agent-os/modules/agent-forms.md`: update SDK skill guidance.
- Modify `tests/test_readiness.py` and `tests/docs/test_production_readiness_docs.py`: lock readiness/docs evidence.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 62.

## Task 1: RED Conformance Tests

- [ ] Add default harness assertions to `tests/channels/test_a2a_conformance.py`:

```python
assert "task-resubscribe-jsonrpc-envelope" in report.check_ids
assert "task-resubscribe-event-shape" in report.check_ids
assert report.finding("task-resubscribe-jsonrpc-envelope").passed is True
assert report.finding("task-resubscribe-event-shape").passed is True
```

- [ ] Add a failing-shape test for a non-resubscribe operation payload:

```python
report = A2AConformanceHarness().run(
    conformance_card(),
    task_resubscribe_request_payload=send_payload,
)
resubscribe_check = report.finding("task-resubscribe-jsonrpc-envelope")
assert report.passed is False
assert resubscribe_check.passed is False
assert "tasks/resubscribe" in resubscribe_check.detail
```

- [ ] Add a failing-shape test for a malformed event payload:

```python
report = A2AConformanceHarness().run(
    conformance_card(),
    task_resubscribe_event_payload={"taskEvent": {"id": "task_1"}},
)
event_check = report.finding("task-resubscribe-event-shape")
assert report.passed is False
assert event_check.passed is False
assert "statusUpdate" in event_check.detail
```

- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: fails because the new check ids and run parameters do not exist yet.

## Task 2: GREEN Harness Implementation

- [ ] Import `a2a_task_subscription_event_from_dict` in `src/agentos/channels/a2a_conformance.py`.
- [ ] Add `task_resubscribe_request_payload` and `task_resubscribe_event_payload` parameters to `A2AConformanceHarness.run(...)`.
- [ ] Add matching fields to `_A2ASamplePayloads` and build defaults with:

```python
a2a_operation_request_to_dict(
    A2AOperationRequest.task_resubscribe(
        "task_1",
        after_event_id=1,
        request_id="task_resubscribe_req_1",
    ),
)
```

and:

```python
a2a_task_subscription_event_to_dict(
    A2ATaskSubscriptionEvent(event_id="2", task=task),
)
```

- [ ] Add `_check_task_resubscribe_jsonrpc_envelope(...)` validating:
  - `jsonrpc == "2.0"`
  - `method == "tasks/resubscribe"`
  - `params` is an object
  - `params.id` is a non-empty string
  - optional `params.afterEventId` is an integer
- [ ] Add `_check_task_resubscribe_event_shape(...)` validating:
  - payload contains a `statusUpdate` object
  - `a2a_task_subscription_event_from_dict(payload)` parses successfully
  - parsed task id is non-empty
- [ ] Add both checks to the report order after message stream checks.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: all conformance tests pass.

## Task 3: Docs And Readiness

- [ ] Add readiness evidence strings:

```python
"tasks/resubscribe self-conformance check",
"task resubscribe event self-conformance check",
```

- [ ] Add test assertions in `tests/test_readiness.py`.
- [ ] Update production readiness docs to say `A2AConformanceHarness` checks
  `tasks/resubscribe request` and `task resubscribe statusUpdate event`.
- [ ] Update agent-os skill guidance with the same self-conformance evidence.
- [ ] Add doc assertions in `tests/docs/test_production_readiness_docs.py`.
- [ ] Append Phase 62 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all readiness and docs tests pass.

## Task 4: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] Run A2A channel regression tests:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_a2a_egress_url_policy.py tests\architecture\test_public_api.py -q
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
