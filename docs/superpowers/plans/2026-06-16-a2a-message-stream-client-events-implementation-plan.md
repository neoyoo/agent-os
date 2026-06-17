# A2A Message Stream Client Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed A2A `message/stream` SSE consumption primitives without taking ownership of reconnect, durable cursor, fan-out, or backpressure policy.

**Architecture:** Keep parsing and client consumption in the A2A channel layer. Extend the transport protocol with an SSE POST primitive, reuse existing request/header/egress helpers, and convert received A2A StreamResponse JSON payloads into typed SDK events.

**Tech Stack:** Python, pytest, urllib, A2A operation serializers.

---

## File Structure

- Modify `src/agentos/channels/a2a.py`: extend `A2ATransport` and `UrllibA2ATransport` with `post_sse(...)`.
- Modify `src/agentos/channels/a2a_operations.py`: add `A2AMessageStreamEvent`, SSE parser helpers, and `A2AOperationClient.stream_message_events(...)`.
- Modify `src/agentos/channels/__init__.py` and `src/agentos/__init__.py`: export public names if needed.
- Modify `tests/channels/test_a2a_operations.py`: parser and client consumption tests.
- Modify `tests/channels/test_a2a_egress_url_policy.py`: blocked outbound stream test.
- Modify readiness/docs/skill/roadmap files to document the new primitive and remaining deployment-owned boundaries.

## Task 1: SSE Parser And Typed Event Model

- [ ] Write a failing parser test for task, message, status update, artifact update, comments, and multi-line data.
- [ ] Run that parser test and verify it fails because parser/model names are missing.
- [ ] Add `A2AMessageStreamEvent`, `parse_a2a_sse_events(...)`, and `a2a_message_stream_event_from_dict(...)`.
- [ ] Run the parser test and verify it passes.

## Task 2: Transport And Client Consumption

- [ ] Write a failing client test using a fake transport with `post_sse(...)`.
- [ ] Run it and verify it fails because `stream_message_events(...)` is missing.
- [ ] Extend `A2ATransport`, implement `UrllibA2ATransport.post_sse(...)`, and add `A2AOperationClient.stream_message_events(...)`.
- [ ] Run the client test and verify it passes.

## Task 3: Security Boundary Tests

- [ ] Write tests proving required unsupported extensions and blocked egress URLs fail before `post_sse(...)`.
- [ ] Run those tests and verify expected failures before implementation if needed.
- [ ] Reuse `_headers_for_card(...)` and `_validate_url(...)` in `stream_message_events(...)`.
- [ ] Run the tests and verify they pass.

## Task 4: Docs And Readiness

- [ ] Add readiness evidence for `A2AMessageStreamEvent` and `A2AOperationClient.stream_message_events`.
- [ ] Update production readiness docs and agent-os skill guidance.
- [ ] Add doc tests that lock the new boundary while keeping reconnect/cursor/backpressure deployment-owned.
- [ ] Append Phase 59 to the roadmap.

## Task 5: Verification

- [ ] Run targeted tests:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_a2a_egress_url_policy.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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
