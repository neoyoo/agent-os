# A2A Message Stream Client Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an outbound `A2AOperationClient.stream_message(...)` primitive that initiates `message/stream` through the same client boundary controls as `message/send`.

**Architecture:** Reuse the existing operation request serializer, client URL validation, header assembly, and response parser. Do not add SSE stream consumption or runtime loop behavior.

**Tech Stack:** Python, pytest, agent-os A2A operation client/server primitives.

---

## File Structure

- Modify `src/agentos/channels/a2a_operations.py`: add `A2AOperationClient.stream_message(...)`.
- Modify `tests/channels/test_a2a_operations.py`: add operation client behavior tests.
- Modify `tests/channels/test_a2a_egress_url_policy.py`: add blocked egress coverage for streaming client calls.
- Modify `src/agentos/readiness.py`, `docs/production-readiness.md`, `.claude/skills/agent-os/modules/agent-forms.md`, `.claude/skills/agent-os/modules/multi-agent.md`, and `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: update capability evidence and guidance.

## Task 1: Client Request Boundary

**Files:**

- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Write the failing test**

Add a test near `test_a2a_operation_client_posts_message_send`:

```python
def test_a2a_operation_client_posts_message_stream() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeTransport()
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="chat", name="Chat", description="Chat."),),
    )
    message = A2AMessage(
        role="user",
        parts=(A2AMessagePart.from_text("hello"),),
        context_id="ctx_1",
    )

    response = client.stream_message(
        card,
        message,
        request_id="req_stream",
        timeout_seconds=11,
        headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    )

    assert response.task is not None
    assert response.task.state == "completed"
    assert transport.posts[0][0] == "https://remote.example/a2a/message:stream"
    assert transport.posts[0][1]["method"] == "message/stream"
    assert transport.posts[0][1]["id"] == "req_stream"
    assert transport.posts[0][2] == 11
    assert transport.posts[0][3] == {
        "A2A-Version": "1.0",
        "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_client_posts_message_stream -q
```

Expected: fail because `A2AOperationClient` has no `stream_message` method.

- [ ] **Step 3: Add minimal implementation**

Add to `A2AOperationClient` after `send_message(...)`:

```python
def stream_message(
    self,
    card: A2AAgentCard,
    message: A2AMessage,
    *,
    request_id: str | int | None = None,
    timeout_seconds: float = 300,
    headers: dict[str, str] | None = None,
) -> A2AOperationResponse:
    """Send message/stream to an A2A agent card URL."""

    request = A2AOperationRequest.message_stream(
        message,
        request_id=request_id,
    )
    payload = a2a_operation_request_to_dict(request)
    url = card.url.rstrip("/") + "/message:stream"
    self._validate_url(url)
    response = self._transport.post_json(
        url,
        payload,
        timeout_seconds,
        headers=self._headers_for_card(card, headers),
    )
    return a2a_operation_response_from_dict(response)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_client_posts_message_stream -q
```

Expected: pass.

## Task 2: Header Negotiation And Pre-Network Rejection

**Files:**

- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Write focused tests**

Add tests that call `client.stream_message(...)` through the existing extension
negotiation setup used by `send_message(...)`:

```python
def test_a2a_operation_client_stream_message_sends_supported_extension_header() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    supported_uri = "https://extensions.example/stream-trace/v1"
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=(supported_uri,),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(A2AAgentExtension(uri=supported_uri),),
        ),
    )

    client.stream_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        timeout_seconds=3,
    )

    assert transport.posts[0][3] == {
        "A2A-Version": "1.0",
        "A2A-Extensions": supported_uri,
    }
```

```python
def test_a2a_operation_client_stream_message_rejects_required_unsupported_extension_before_network() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationError,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    required_uri = "https://extensions.example/stream-required/v1"
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=("https://extensions.example/known/v1",),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(A2AAgentExtension(uri=required_uri, required=True),),
        ),
    )

    with pytest.raises(
        A2AExtensionNegotiationError,
        match="requires unsupported extensions",
    ):
        client.stream_message(
            card,
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.posts == []
```

- [ ] **Step 2: Run tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_client_stream_message_sends_supported_extension_header tests\channels\test_a2a_operations.py::test_a2a_operation_client_stream_message_rejects_required_unsupported_extension_before_network -q
```

Expected: pass after Task 1 implementation because both tests exercise shared helpers.

## Task 3: Egress Policy Coverage

**Files:**

- Modify: `tests/channels/test_a2a_egress_url_policy.py`

- [ ] **Step 1: Write egress test**

Add:

```python
def test_a2a_operation_client_stream_message_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AAgentCard,
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = RecordingTransport()
    client = A2AOperationClient(
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        client.stream_message(
            A2AAgentCard(
                name="Blocked",
                description="Blocked peer.",
                url="https://blocked.example/a2a",
                version="1.0.0",
            ),
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.calls == []
```

- [ ] **Step 2: Run egress test**

Run:

```powershell
uv run pytest tests\channels\test_a2a_egress_url_policy.py::test_a2a_operation_client_stream_message_enforces_egress_policy_before_transport -q
```

Expected: pass after Task 1 implementation.

## Task 4: Documentation And Readiness

**Files:**

- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Update readiness evidence**

Add evidence that A2A operation clients can initiate `message/stream`. Do not
mark full A2A production complete.

- [ ] **Step 2: Update docs and skill guidance**

Mention `A2AOperationClient.stream_message(...)` as the outbound initiation
primitive. Keep the long-lived SSE client and durable stream cursor work listed
as deployment-owned.

- [ ] **Step 3: Update roadmap**

Append Phase 58 with target conclusion, artifacts, and final conclusion.

- [ ] **Step 4: Run documentation/readiness tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: pass.

## Task 5: Verification

- [ ] **Step 1: Run targeted suite**

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_a2a_egress_url_policy.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] **Step 2: Run full suite**

```powershell
uv run pytest -q
```

- [ ] **Step 3: Compile**

```powershell
uv run python -m compileall -q src tests
```

- [ ] **Step 4: Runtime boundary scan**

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: exit code 1 with no output.

- [ ] **Step 5: Diff hygiene**

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if the command exits 0.
