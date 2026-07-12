# A2A Per-Peer Rate Limit Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned inbound A2A operation rate-limit boundary keyed by authenticated peer, operation, task, and resource context.

**Architecture:** Keep the boundary in `agentos.channels.a2a_operations`. `A2AOperationServer` authorizes the request first, then asks an injected `A2AOperationRateLimitPolicy` before it calls the runner, task lifecycle, or push notification config store. Reuse the existing channel `RateLimiter` protocol; keep distributed quota stores and gateway enforcement deployment-owned.

**Tech Stack:** Python dataclasses/protocols, existing A2A operation server, existing `RateLimiter` and `SlidingWindowRateLimiter`, pytest.

Spec: `docs/superpowers/specs/2026-06-16-a2a-per-peer-rate-limit-boundary-design.md`

---

### Task 1: RED Tests For A2A Operation Rate Limiting

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Add a peer resolver fake and message/send rate-limit test**

Add tests that import the wished-for API:

```python
def test_a2a_operation_server_rate_limits_per_peer_before_runner() -> None:
    from agentos.channels.a2a import StaticBearerA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        A2APeerIdResolver,
        AgentA2AOperationRunner,
        PeerKeyA2AOperationRateLimitPolicy,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )
    from agentos.channels.rate_limit import SlidingWindowRateLimiter

    class HeaderPeerResolver:
        def peer_id_for_headers(self, headers: dict[str, str]) -> str | None:
            return headers.get("X-A2A-Peer")

    class CountingRunner(AgentA2AOperationRunner):
        def __init__(self) -> None:
            super().__init__(build_agent_with_response("allowed"))
            self.calls = 0

        def send_message(self, message: A2AMessage):
            self.calls += 1
            return super().send_message(message)

    runner = CountingRunner()
    server = A2AOperationServer(
        runner,
        inbound_auth_policy=StaticBearerA2AInboundAuthPolicy("peer-token"),
        rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy(
            peer_id_resolver=HeaderPeerResolver(),
            rate_limiter=SlidingWindowRateLimiter(max_requests=1, window_seconds=60),
        ),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_rate",
        ),
    )
    headers = {
        "Authorization": "Bearer peer-token",
        "X-A2A-Peer": "peer-a",
    }

    allowed = a2a_operation_response_from_dict(
        server.handle_operation(payload, headers=headers),
    )
    denied = a2a_operation_response_from_dict(
        server.handle_operation(payload, headers=headers),
    )
    other_peer = a2a_operation_response_from_dict(
        server.handle_operation(
            payload,
            headers={
                "Authorization": "Bearer peer-token",
                "X-A2A-Peer": "peer-b",
            },
        ),
    )

    assert allowed.error is None
    assert denied.error is not None
    assert denied.error.code == -32029
    assert denied.error.message == "rate limit exceeded"
    assert denied.error.data == {
        "type": "https://a2a-protocol.org/errors/rate-limit-exceeded",
        "title": "Rate Limit Exceeded",
        "status": 429,
        "retryAfterSeconds": 60,
    }
    assert other_peer.error is None
    assert runner.calls == 2
```

- [ ] **Step 2: Add operation/task/resource key tests**

Add tests that prove the policy passes distinct keys to a fake limiter:

```python
def test_a2a_operation_rate_limit_keys_include_peer_operation_and_resource() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        A2APushNotificationConfig,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
        PeerKeyA2AOperationRateLimitPolicy,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.rate_limit import RateLimitDecision

    class HeaderPeerResolver:
        def peer_id_for_headers(self, headers: dict[str, str]) -> str | None:
            return headers.get("X-A2A-Peer")

    class RecordingLimiter:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def check(self, key: str) -> RateLimitDecision:
            self.keys.append(key)
            return RateLimitDecision(True, 0)

    task_store = TaskTable()
    task_store.create(task_record(task_id="task_1", status="queued"))
    limiter = RecordingLimiter()
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(task_store),
        push_notification_configs=InMemoryA2APushNotificationConfigStore(),
        rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy(
            peer_id_resolver=HeaderPeerResolver(),
            rate_limiter=limiter,
        ),
    )
    headers = {"X-A2A-Peer": "peer-a"}

    server.handle_task_get("task_1", headers=headers)
    server.handle_push_notification_config_create(
        "task_1",
        A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        headers=headers,
    )

    assert limiter.keys == [
        "a2a:peer=peer-a:operation=tasks/get:task=task_1:resource=task/task_1",
        (
            "a2a:peer=peer-a:operation=pushNotificationConfigs/create:"
            "task=task_1:resource=pushNotificationConfig/cfg_1"
        ),
    ]
```

- [ ] **Step 3: Run RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: fail because `A2APeerIdResolver`, `PeerKeyA2AOperationRateLimitPolicy`, or `rate_limit_policy` does not exist.

### Task 2: Implement The Operation Rate-Limit Boundary

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Add imports and public primitives**

Add `Callable` if needed and import `RateLimitDecision` and `RateLimiter` from `agentos.channels.rate_limit`.

Add near the operation error/request dataclasses:

```python
class A2ARateLimitError(RuntimeError):
    """Raised when an inbound A2A operation exceeds a configured rate policy."""

    def __init__(self, retry_after_seconds: int = 0) -> None:
        self.retry_after_seconds = max(0, int(retry_after_seconds))
        super().__init__("rate limit exceeded")


class A2APeerIdResolver(Protocol):
    """Boundary for resolving an authenticated inbound A2A peer id."""

    def peer_id_for_headers(self, headers: Mapping[str, str]) -> str | None:
        """Return the peer id for request headers, or None when unknown."""


class A2AOperationRateLimitPolicy(Protocol):
    """Boundary for inbound A2A operation rate limiting."""

    def check_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Raise A2ARateLimitError when an operation should be throttled."""
```

- [ ] **Step 2: Add peer-keyed policy**

Add:

```python
@dataclass(frozen=True, slots=True)
class PeerKeyA2AOperationRateLimitPolicy:
    """Rate-limit inbound A2A operations by peer, operation, task, and resource."""

    peer_id_resolver: A2APeerIdResolver
    rate_limiter: RateLimiter
    key_prefix: str = "a2a"

    def check_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        peer_id = self.peer_id_resolver.peer_id_for_headers(headers)
        if peer_id is None or not str(peer_id).strip():
            raise A2ARateLimitError()
        key = self.key_for_operation(
            str(peer_id),
            operation=operation,
            task_id=task_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        decision = self.rate_limiter.check(key)
        if not decision.allowed:
            raise A2ARateLimitError(decision.retry_after_seconds)

    def key_for_operation(
        self,
        peer_id: str,
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> str:
        parts = [
            self._key_part(str(self.key_prefix).strip() or "a2a"),
            f"peer={self._key_part(peer_id)}",
            f"operation={self._key_part(operation)}",
        ]
        if task_id:
            parts.append(f"task={self._key_part(task_id)}")
        if resource_type:
            resource = self._key_part(resource_type)
            if resource_id:
                resource = f"{resource}/{self._key_part(resource_id)}"
            parts.append(f"resource={resource}")
        return ":".join(parts)

    @staticmethod
    def _key_part(value: object) -> str:
        text = str(value).strip()
        if not text:
            return "_"
        return (
            text.replace("\\", "_")
            .replace("/", "_")
            .replace(":", "_")
            .replace(" ", "_")
        )
```

Use the exact slash behavior expected by tests, or adjust tests and implementation consistently if sanitization policy changes.

- [ ] **Step 3: Wire policy into server**

Extend `A2AOperationServer.__init__` with:

```python
rate_limit_policy: A2AOperationRateLimitPolicy | None = None,
```

Store it as `self._rate_limit_policy`.

Add `_rate_limit(...)` and `_rate_limit_response(...)`, mirroring `_authorize(...)` and `_authorize_response(...)`. Call `_rate_limit_response(...)` immediately after auth success in every operation handler and before validation that would hit runner/store work when the operation is otherwise known.

For `handle_operation`, use the parsed request id in the rate-limit response.

- [ ] **Step 4: Add structured error response**

Add:

```python
def _rate_limit_exceeded_response(
    self,
    request_id: str | int | None,
    error: A2ARateLimitError,
) -> dict[str, object]:
    retry_after = error.retry_after_seconds
    return self._error_response(
        request_id,
        A2AOperationError(
            code=-32029,
            message="rate limit exceeded",
            data={
                "type": "https://a2a-protocol.org/errors/rate-limit-exceeded",
                "title": "Rate Limit Exceeded",
                "status": 429,
                "retryAfterSeconds": retry_after,
            },
        ),
    )
```

- [ ] **Step 5: Run GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: pass.

### Task 3: Public API Exports

**Files:**
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`

- [ ] **Step 1: Add public API expectations**

Add these names to channel and top-level public API tests:

```python
"A2APeerIdResolver",
"A2AOperationRateLimitPolicy",
"A2ARateLimitError",
"PeerKeyA2AOperationRateLimitPolicy",
```

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: fail until exports are added.

- [ ] **Step 3: Export the names**

Import and include the four names from `agentos.channels.a2a_operations` in `agentos.channels.__all__`, then import and include them in top-level `agentos.__all__`.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: pass.

### Task 4: Readiness, Docs, Skill, Roadmap

**Files:**
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Add readiness and docs expectations**

Update tests so the A2A readiness `rate_limit` dimension contains `PeerKeyA2AOperationRateLimitPolicy` and no longer says per-peer A2A rate policy is app-owned. It must still say distributed/global quotas remain deployment-owned.

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: fail until docs/readiness are updated.

- [ ] **Step 3: Update readiness and docs**

Update A2A readiness evidence with:

```text
A2AOperationRateLimitPolicy
PeerKeyA2AOperationRateLimitPolicy
A2ARateLimitError
```

Change the gap from app-owned per-peer rate policy to deployment-owned distributed/global quotas and gateway/billing policy.

- [ ] **Step 4: Update skill guidance and roadmap**

Add Phase 55 to the roadmap. Update agent-os skill modules to recommend `PeerKeyA2AOperationRateLimitPolicy` for public A2A services while preserving the deployment-owned distributed quota caveat.

- [ ] **Step 5: Run GREEN**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: pass.

### Task 5: Final Verification

**Files:**
- No new edits unless verification exposes a real defect.

- [ ] **Step 1: Run targeted verification**

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] **Step 2: Run full test suite**

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

Expected: exit code `1` with no output.

- [ ] **Step 5: Diff hygiene**

```powershell
git diff --check
```

Expected: exit code `0`; CRLF warnings are acceptable in this long-running branch.
