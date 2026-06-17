# A2A Message Stream Conformance Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend SDK-owned A2A self-conformance so it checks `message/stream` JSON-RPC request shape and typed SSE stream event parsing.

**Architecture:** Keep all checks in `agentos.channels.a2a_conformance`. Reuse the existing operation serializers and SSE parser so the harness verifies public SDK protocol primitives instead of duplicating transport logic. Keep external suite execution, reconnect, backpressure, durable cursors, fan-out, gateway quota, and certification out of scope.

**Tech Stack:** Python dataclasses, pytest, existing A2A channel serializers.

---

## File Structure

- Modify `src/agentos/channels/a2a_conformance.py`: add stream sample payloads, two harness checks, and optional run arguments.
- Modify `tests/channels/test_a2a_conformance.py`: add RED tests for passing and failing stream conformance checks.
- Modify `src/agentos/readiness.py`: add stream self-conformance evidence.
- Modify `docs/production-readiness.md`: document stream self-conformance.
- Modify `.claude/skills/agent-os/modules/agent-forms.md` and `.claude/skills/agent-os/modules/multi-agent.md`: update skill guidance.
- Modify `tests/test_readiness.py` and `tests/docs/test_production_readiness_docs.py`: lock docs/readiness evidence.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 60.

## Task 1: RED Tests For Stream Self-Conformance

- [ ] Add a test that default `A2AConformanceHarness().run(conformance_card())`
  includes passing `message-stream-jsonrpc-envelope` and
  `message-stream-event-shape` findings.
- [ ] Add a test that passing a `message/send` payload as
  `stream_operation_request_payload` fails `message-stream-jsonrpc-envelope`.
- [ ] Add a test that a malformed stream event payload fails
  `message-stream-event-shape`.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: fails because the new check ids and run arguments do not exist.

## Task 2: GREEN Implementation

- [ ] Add `A2AOperationRequest.message_stream(...)`,
  `a2a_operation_request_to_dict(...)`, and `parse_a2a_sse_events(...)`
  imports to `a2a_conformance.py`.
- [ ] Add `stream_operation_request_payload` and `stream_event_payload` to
  `_A2ASamplePayloads`.
- [ ] Add `_check_message_stream_jsonrpc_envelope(...)`.
- [ ] Add `_check_message_stream_event_shape(...)`.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: all conformance tests pass.

## Task 3: Docs And Readiness

- [ ] Add readiness evidence for `message/stream self-conformance check`.
- [ ] Add docs/skill text that the self-conformance harness checks stream
  request and typed SSE event primitives.
- [ ] Add doc/readiness tests for that evidence.
- [ ] Append Phase 60 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all pass.

## Task 4: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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
