# A2A Bearer Credential Rotation Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow A2A bearer credential rotation boundary for outbound token selection, inbound overlap acceptance, generic rejection, and secret redaction.

**Architecture:** The feature lives in `src/agentos/channels/a2a.py` beside existing A2A auth policies. It does not touch runtime loops; deployment-owned secret issuance, secret distribution, KMS, approval workflow, and audit governance stay outside the SDK.

**Tech Stack:** Python 3.11 dataclasses/protocols, pytest, existing `agentos.channels.a2a` auth abstractions.

---

## File Structure

- Modify `tests/channels/test_a2a_operations.py`: RED tests for outbound current-token selection, inbound overlap acceptance, denied states, operation/resource allow-lists, and repr redaction.
- Modify `src/agentos/channels/a2a.py`: add `A2ACredentialRotationError`, `A2ABearerCredential`, `RotatingBearerA2ACredentialStore`, `RotatingBearerA2AAuthProvider`, and `RotatingBearerA2AInboundAuthPolicy`.
- Modify `src/agentos/channels/__init__.py`: export the new channel auth primitives.
- Modify `src/agentos/__init__.py`: export the new public top-level SDK names.
- Modify `tests/architecture/test_public_api.py`: assert the new public API names.
- Modify `src/agentos/readiness.py`: move credential rotation from app glue gap into SDK evidence while keeping secret issuance/distribution governance deployment-owned.
- Modify `tests/test_readiness.py`: verify readiness evidence and remaining deployment-owned gap text.
- Modify `docs/production-readiness.md`: document the rotating bearer store/provider/policy and remaining deployment responsibilities.
- Modify `.claude/skills/agent-os/modules/agent-forms.md`: update A2A capability guidance.
- Modify `.claude/skills/agent-os/modules/multi-agent.md`: update remaining A2A auth gap guidance.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 54 outcome.

## Scope Contract

- Phase: A2A enterprise auth hardening Phase 54.
- Completes: rotating bearer outbound current token, inbound overlap token acceptance, expired/not-yet-valid/revoked rejection, secret redaction, public API export, readiness/docs/skill/roadmap updates.
- Deferred: KMS, secret-manager integration, automatic secret distribution, audit approval workflow, CA trust rollout, DNS pinning/egress proxy, and external conformance execution.
- Boundary rule: no A2A/auth/planner concepts may be added to `src/agentos/runtime/query_loop.py` or `src/agentos/runtime/async_query_loop.py`.

## Task 1: RED Tests

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Add failing tests**

Add tests that describe the desired public API:

```python
def test_rotating_bearer_auth_provider_uses_current_active_credential() -> None:
    from agentos.channels.a2a import (
        A2ABearerCredential,
        RotatingBearerA2AAuthProvider,
        RotatingBearerA2ACredentialStore,
    )

    store = RotatingBearerA2ACredentialStore(
        credentials=(
            A2ABearerCredential(
                key_id="old",
                peer_id="remote",
                token="old-token",
                not_before=10,
                not_after=30,
            ),
            A2ABearerCredential(
                key_id="new",
                peer_id="remote",
                token="new-token",
                not_before=20,
                not_after=50,
            ),
        ),
        current_key_id="new",
        clock=lambda: 25.0,
    )
    provider = RotatingBearerA2AAuthProvider(store)

    assert provider.headers_for_card(A2AAgentCard(name="Remote", url="https://remote.example")) == {
        "Authorization": "Bearer new-token",
    }
    assert "new-token" not in repr(store)
    assert "old-token" not in repr(provider)
```

Add one test for inbound overlap acceptance and generic rejection, plus one test for operation/resource allow-lists.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: failure because `A2ABearerCredential` and rotating bearer classes are not yet importable.

## Task 2: Core Channel Auth Implementation

**Files:**
- Modify: `src/agentos/channels/a2a.py`

- [ ] **Step 1: Add minimal implementation**

Implement the new names near the existing static bearer auth classes:

```python
class A2ACredentialRotationError(ValueError):
    """Raised when a rotating A2A bearer credential cannot be used."""


@dataclass(frozen=True, repr=False, slots=True)
class A2ABearerCredential:
    key_id: str
    peer_id: str
    token: str
    not_before: float | None = None
    not_after: float | None = None
    revoked: bool = False

    def is_active(self, now: float) -> bool:
        ...
```

Store behavior:

```python
store.current_credential()       # returns active current credential or raises A2ACredentialRotationError
store.peer_id_for_token(token)   # returns peer id for active token or None
store.active_key_ids()           # returns active key ids for diagnostics
```

Inbound policy behavior:

```python
policy.authorize(headers)
policy.authorize_operation(headers, operation="message/send")
policy.authorize_resource(headers, operation="tasks/get", task_id="task_1")
```

Implementation requirements:
- validate non-empty `key_id`, `peer_id`, and `token`
- validate `not_after >= not_before` when both are set
- use `hmac.compare_digest` for token matching
- reject missing/malformed/unknown/expired/future/revoked tokens with `A2AInboundAuthError("unauthorized peer")`
- keep tokens out of every `repr`

- [ ] **Step 2: Run channel tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -q
```

Expected: all A2A operation tests pass.

## Task 3: Public API Exports

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add public API assertions**

Add these names to the channel and top-level API expected sets:

```python
"A2ABearerCredential",
"A2ACredentialRotationError",
"RotatingBearerA2AAuthProvider",
"RotatingBearerA2ACredentialStore",
"RotatingBearerA2AInboundAuthPolicy",
```

- [ ] **Step 2: Export names**

Import and include the same names in both `__all__` lists.

- [ ] **Step 3: Run public API tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: public API tests pass.

## Task 4: Readiness and Documentation

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Update readiness evidence and tests**

Readiness evidence for A2A auth must include:

```python
"A2ABearerCredential",
"RotatingBearerA2ACredentialStore",
"RotatingBearerA2AAuthProvider",
"RotatingBearerA2AInboundAuthPolicy",
```

The remaining app glue gap should name deployment-owned `credential issuance and secret distribution policy` or equivalent KMS/secret-manager governance wording, not SDK credential rotation.

- [ ] **Step 2: Update docs and skill guidance**

Document that rotating bearer credentials cover overlap windows inside the SDK, while real secret creation, distribution, revocation approval, audit, and KMS/secret-manager rollout stay deployment-owned.

- [ ] **Step 3: Run docs/readiness tests**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: readiness and production docs tests pass.

## Task 5: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted verification**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: targeted tests pass.

- [ ] **Step 2: Run full verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth|Egress|Health|Dispatch|Conformance|Team|DistributedTeamRuntimeProfile|Tenant|RBAC" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected:
- pytest passes
- compileall passes
- runtime boundary scan returns exit code 1 with no output
- `git diff --check` exits 0; CRLF warnings are acceptable if the exit code is 0

## Self-Review

- Spec coverage: outbound current token, inbound overlap, denial states, allow-lists, public API, readiness/docs/skill/roadmap, and runtime boundary verification are covered by tasks.
- Placeholder scan: no implementation step relies on an unspecified later step.
- Type consistency: public names match the spec and export assertions.
