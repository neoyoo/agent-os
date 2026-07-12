# A2A Extension Negotiation Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned A2A extension negotiation boundary for Agent Card extensions and the `A2A-Extensions` operation header.

**Architecture:** Implement a focused policy in `agentos.channels.a2a_operations` and inject it into A2A operation client/server paths. Keep extension negotiation at the channel/protocol layer and leave runtime loops unaware of A2A semantics.

**Tech Stack:** Python dataclasses, existing A2A operation serializers, pytest.

---

### Task 1: RED Tests For Server Negotiation

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that a server with a required extension returns JSON-RPC
`-32008` when the peer omits `A2A-Extensions`, and succeeds when the header
contains the required URI.

- [ ] **Step 2: Run targeted tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_server_requires_declared_extensions -q
```

Expected: FAIL because `A2AExtensionNegotiationPolicy` does not exist.

### Task 2: RED Tests For Client Negotiation

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that `A2AOperationClient` emits `A2A-Extensions` for configured
supported peer extensions, and raises before transport when the peer card
requires an unsupported extension.

- [ ] **Step 2: Run targeted tests**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_client_sends_supported_extension_header -q
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_client_rejects_required_unsupported_extension_before_network -q
```

Expected: FAIL because the policy and header handling do not exist.

### Task 3: Implement Policy And Server Integration

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Add constants, result, error, and policy**

Define `A2A_EXTENSIONS_HEADER`, `A2AExtensionNegotiationResult`,
`A2AExtensionNegotiationError`, and `A2AExtensionNegotiationPolicy`.

- [ ] **Step 2: Integrate policy into `A2AOperationServer`**

Call negotiation after protocol-version validation and before operation
execution for message, task lifecycle, and push notification config routes.

- [ ] **Step 3: Verify server tests pass**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_server_requires_declared_extensions -q
```

Expected: PASS.

### Task 4: Implement Client Header Generation

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Add `extension_negotiation_policy` to `A2AOperationClient`**

Use the policy inside `_headers_for_card(...)` to add or merge
`A2A-Extensions`.

- [ ] **Step 2: Verify client tests pass**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_client_sends_supported_extension_header tests\channels\test_a2a_operations.py::test_a2a_operation_client_rejects_required_unsupported_extension_before_network -q
```

Expected: PASS.

### Task 5: Public API And Readiness Docs

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Export new policy and error classes**

Add `A2AExtensionNegotiationPolicy`, `A2AExtensionNegotiationError`, and
`A2AExtensionNegotiationResult` to channel and top-level exports.

- [ ] **Step 2: Update readiness evidence and docs**

List extension negotiation as available; keep external conformance and trust
governance as remaining gaps.

- [ ] **Step 3: Run docs/readiness/public API tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: PASS.

### Task 6: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted A2A tests**

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_asgi_app.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] **Step 2: Run full test suite**

```powershell
uv run pytest -q
```

- [ ] **Step 3: Compile and boundary scan**

```powershell
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: pytest and compileall pass; `rg` exits 1 with no matches; diff check
exits 0 aside from existing CRLF warnings.
