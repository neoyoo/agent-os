# A2A Message Stream Operation Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned A2A `message/stream` operation boundary and ASGI SSE route that reuse existing A2A auth, version, extension, trace, task subscription, and rate-limit controls.

**Architecture:** Extend the existing `A2AOperationRequest` and `A2AOperationServer` rather than introducing a new streaming subsystem. ASGI owns the long-lived SSE transport and delegates operation initiation plus follow-up task updates to `A2AOperationServer`.

**Tech Stack:** Python dataclasses/protocols, ASGI SSE, existing A2A operation serializers, pytest.

---

## File Structure

- Modify `src/agentos/channels/a2a_operations.py` for request factory, operation dispatch, explicit handler, and outbound client helper.
- Modify `src/agentos/channels/asgi.py` for `POST /a2a/message:stream`.
- Modify `tests/channels/test_a2a_operations.py` for RED/GREEN operation behavior.
- Modify `tests/channels/test_asgi_app.py` for RED/GREEN ASGI boundary behavior.
- Modify `src/agentos/readiness.py`, `docs/production-readiness.md`, `.claude/skills/agent-os/modules/agent-forms.md`, `.claude/skills/agent-os/modules/multi-agent.md`, and `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md` after code behavior is green.

## Task 1: Operation Request And Server Boundary

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Write failing request round-trip test**

Add a test beside `test_a2a_task_resubscribe_operation_request_response_round_trip`:

```python
def test_a2a_message_stream_operation_request_round_trip() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        a2a_operation_request_from_dict,
        a2a_operation_request_to_dict,
    )

    message = A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),))
    request = A2AOperationRequest.message_stream(message, request_id="req_stream")

    assert a2a_operation_request_to_dict(request) == {
        "jsonrpc": "2.0",
        "id": "req_stream",
        "method": "message/stream",
        "params": {"message": {"role": "user", "parts": [{"text": "hello"}]}},
    }
    assert (
        a2a_operation_request_from_dict(a2a_operation_request_to_dict(request))
        == request
    )
```

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_message_stream_operation_request_round_trip -q
```

Expected: fail because `A2AOperationRequest.message_stream` does not exist.

- [ ] **Step 3: Implement request factory**

Add to `A2AOperationRequest`:

```python
    @classmethod
    def message_stream(
        cls,
        message: A2AMessage,
        *,
        request_id: str | int | None = None,
    ) -> A2AOperationRequest:
        """Create a message/stream request."""

        return cls(
            method="message/stream",
            params={"message": a2a_message_to_dict(message)},
            request_id=request_id,
        )
```

- [ ] **Step 4: Verify GREEN**

Run the same test. Expected: pass.

- [ ] **Step 5: Write failing server handling test**

Add a test near `test_a2a_operation_server_handles_message_send`:

```python
def test_a2a_operation_server_handles_message_stream_json_rpc() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("stream ok")),
    )

    response = a2a_operation_response_from_dict(
        server.handle_operation(
            a2a_operation_request_to_dict(
                A2AOperationRequest.message_stream(
                    A2AMessage(
                        role="user",
                        parts=(A2AMessagePart.from_text("hello"),),
                    ),
                    request_id="req_stream",
                ),
            ),
        ),
    )

    assert response.request_id == "req_stream"
    assert response.error is None
    assert response.task is not None
    assert response.task.state == "completed"
    assert response.task.messages[-1].parts[0].text == "stream ok"
```

- [ ] **Step 6: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_server_handles_message_stream_json_rpc -q
```

Expected: fail with unsupported method.

- [ ] **Step 7: Implement explicit handler**

Add `handle_message_stream(...)` to `A2AOperationServer` and route `message/stream` through it from `handle_operation(...)`. Reuse the same parsing and runner call as `message/send`, but pass operation `"message/stream"` into auth and rate limiting.

- [ ] **Step 8: Verify operation tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_message_stream_operation_request_round_trip tests\channels\test_a2a_operations.py::test_a2a_operation_server_handles_message_stream_json_rpc -q
```

Expected: both pass.

## Task 2: Auth And Rate-Limit Semantics

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Extend operation key test**

Update `test_a2a_operation_rate_limit_keys_include_peer_operation_and_resource` to call `server.handle_operation(...)` with `A2AOperationRequest.message_stream(...)` before task operations, and assert the first key is:

```text
a2a:peer=peer-a:operation=message_stream
```

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_rate_limit_keys_include_peer_operation_and_resource -q
```

Expected: fail until `message/stream` rate limiting uses its own operation key.

- [ ] **Step 3: Implement operation-specific boundary**

Refactor `A2AOperationServer` with a private helper that accepts `operation` and payload, or implement `handle_message_stream(...)` directly with version, extension, auth, rate limit, message parse, trace context, and runner call.

- [ ] **Step 4: Verify targeted operation behavior**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: pass.

## Task 3: ASGI Message Stream Route

**Files:**
- Modify: `tests/channels/test_asgi_app.py`
- Modify: `src/agentos/channels/asgi.py`

- [ ] **Step 1: Write failing ASGI auth boundary test**

Add a test proving `POST /a2a/message:stream` returns JSON auth errors before SSE starts when operation auth denies the peer.

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_asgi_app.py::test_asgi_app_a2a_message_stream_uses_operation_inbound_auth_boundary -q
```

Expected: fail because the route does not exist or does not call the operation boundary.

- [ ] **Step 3: Write failing ASGI success stream test**

Add a test proving `POST /a2a/message:stream` starts SSE only after successful operation handling and emits the initial task response.

- [ ] **Step 4: Implement route**

Add `_match_a2a_message_stream(...)` and `_handle_a2a_message_stream(...)` to ASGI. The handler reads the body, calls `self._a2a_operations.handle_operation(...)`, returns JSON for errors, starts SSE for success, sends the initial `A2AOperationResponse(task=...)`, then follows task updates through `handle_task_resubscribe(...)` if a task lifecycle runner is configured.

- [ ] **Step 5: Verify ASGI targeted tests**

Run:

```powershell
uv run pytest tests\channels\test_asgi_app.py -q
```

Expected: pass.

## Task 4: Documentation And Readiness

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`

- [ ] **Step 1: Update readiness evidence**

Add evidence for `message/stream operation` and `message stream operation boundary`.

- [ ] **Step 2: Update production docs and skill guidance**

Describe `message/stream` as an SDK-owned operation boundary and ASGI SSE route. Keep fan-out, backpressure, durable stream cursor, global quota, billing, and deployment supervision as missing/deployment-owned.

- [ ] **Step 3: Update roadmap**

Append Phase 57 with target conclusion, artifacts, and final conclusion.

- [ ] **Step 4: Verify docs/readiness tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: pass.

## Task 5: Full Verification

**Files:**
- No edits.

- [ ] **Step 1: Run targeted suite**

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_asgi_app.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: pass.

- [ ] **Step 2: Run full suite**

```powershell
uv run pytest -q
```

Expected: pass or only known skipped tests.

- [ ] **Step 3: Compile**

```powershell
uv run python -m compileall -q src tests
```

Expected: exit code 0.

- [ ] **Step 4: Runtime boundary scan**

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] **Step 5: Diff hygiene**

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if exit code is 0.
