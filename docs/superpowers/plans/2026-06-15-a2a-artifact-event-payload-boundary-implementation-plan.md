# A2A Artifact/Event Payload Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add A2A 1.0 task artifact and task event wrapper payload support while keeping runtime loops protocol-agnostic.

**Architecture:** Extend `agentos.channels.a2a_operations` with an artifact dataclass and event wrapper serializers/parsers. Reuse the existing A2A message part serializer for artifact parts. Keep ASGI and push notification routes using the same serializer helpers.

**Tech Stack:** Python dataclasses, pytest, existing agent-os A2A operation serializers and ASGI SSE channel.

---

Spec: `docs/superpowers/specs/2026-06-15-a2a-artifact-event-payload-boundary-design.md`

## Tasks

### Task 1: Artifact And Event Tests

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`
- Modify: `tests/channels/test_asgi_app.py`

- [ ] **Step 1: Write failing artifact tests**

Add tests for `A2AArtifact`, `a2a_artifact_to_dict(...)`,
`a2a_artifact_from_dict(...)`, `A2ATask` artifact serialization, and projection
from `TaskResult.artifacts`.

- [ ] **Step 2: Write failing event wrapper tests**

Add tests expecting `a2a_task_subscription_event_to_dict(...)` to emit
`statusUpdate`, expecting legacy `kind: status-update` input to still parse,
and expecting `A2ATaskArtifactUpdateEvent` to emit `artifactUpdate`.

- [ ] **Step 3: Write failing ASGI/push tests**

Update/extend existing ASGI subscribe and push notification tests so their
payload data uses the same `statusUpdate` wrapper shape.

- [ ] **Step 4: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_artifact_round_trip_uses_parts_shape tests\channels\test_a2a_operations.py::test_a2a_task_serializes_artifacts_as_protocol_objects tests\channels\test_a2a_operations.py::test_a2a_task_subscription_event_uses_status_update_wrapper tests\channels\test_a2a_operations.py::test_a2a_task_artifact_update_event_uses_artifact_update_wrapper -q
```

Expected: FAIL because the artifact dataclass, artifact helpers, and
artifact-update event do not exist yet, and status events still emit the legacy
shape.

### Task 2: A2A Artifact/Event Implementation

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`
- Modify: `src/agentos/channels/asgi.py` only if the existing SSE helper needs
  wrapper-aware serialization.

- [ ] **Step 1: Add `A2AArtifact` and helpers**

Create `A2AArtifact`, `a2a_artifact_to_dict(...)`, and
`a2a_artifact_from_dict(...)`. Reuse `A2AMessagePart` serialization for parts.

- [ ] **Step 2: Update task artifact serialization**

Make `a2a_task_to_dict(...)` emit protocol artifact objects. Make
`a2a_task_from_dict(...)` parse them back.

- [ ] **Step 3: Update internal task result projection**

Make `a2a_task_from_task_record(...)` convert `TaskResult.artifacts` into an
`A2AArtifact` with structured data parts.

- [ ] **Step 4: Add event wrapper serializers**

Make status update serialization emit `statusUpdate`, keep legacy inbound
parsing, add `A2ATaskArtifactUpdateEvent`, and add artifact-update serializer
and parser helpers.

- [ ] **Step 5: Verify GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_artifact_round_trip_uses_parts_shape tests\channels\test_a2a_operations.py::test_a2a_task_serializes_artifacts_as_protocol_objects tests\channels\test_a2a_operations.py::test_a2a_task_subscription_event_uses_status_update_wrapper tests\channels\test_a2a_operations.py::test_a2a_task_artifact_update_event_uses_artifact_update_wrapper -q
```

Expected: PASS.

### Task 3: Guidance And Readiness

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`

- [ ] **Step 1: Update readiness evidence**

Add A2A artifact/event wrapper parity to protocol evidence and remove it from
remaining protocol gaps.

- [ ] **Step 2: Update docs and skill guidance**

Describe artifact/event wrapper parity as available, but keep extension
negotiation, external conformance, and deployment trust/governance as remaining
gaps.

- [ ] **Step 3: Add roadmap Phase 44**

Record target conclusion, artifacts, and final conclusion.

### Task 4: Verification

**Files:**
- No code edits.

- [ ] **Step 1: Run targeted tests**

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_asgi_app.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] **Step 2: Run full tests**

```powershell
uv run pytest -q
```

- [ ] **Step 3: Compile**

```powershell
uv run python -m compileall -q src tests
```

- [ ] **Step 4: Check runtime boundary**

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches. `rg` exits `1` for no matches.

- [ ] **Step 5: Check diff hygiene**

```powershell
git diff --check
```

Expected: exit code 0. Existing CRLF warnings are acceptable.
