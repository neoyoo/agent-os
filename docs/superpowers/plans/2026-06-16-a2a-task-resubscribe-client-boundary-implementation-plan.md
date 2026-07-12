# A2A Task Resubscribe Client Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an outbound A2A client boundary for one-shot `tasks/resubscribe` cursor requests.

**Architecture:** Reuse the existing A2A operation request/response serializers and the `A2AOperationClient` header, extension, and egress helpers. Keep reconnect loops, durable cursor storage, fan-out, backpressure, gateway quota, billing, and credential issuance outside SDK core.

**Tech Stack:** Python, pytest, existing A2A channel serializers.

---

## File Structure

- Modify `src/agentos/channels/a2a_operations.py`: add `A2AOperationClient.task_resubscribe(...)`.
- Modify `tests/channels/test_a2a_operations.py`: add post/response and extension pre-network tests.
- Modify `tests/channels/test_a2a_egress_url_policy.py`: add egress pre-network test.
- Modify `src/agentos/readiness.py`: add client task resubscribe evidence.
- Modify `docs/production-readiness.md`: document one-shot cursor resubscribe client boundary.
- Modify `.claude/skills/agent-os/modules/agent-forms.md` and `.claude/skills/agent-os/modules/multi-agent.md`: update skill guidance.
- Modify `tests/test_readiness.py` and `tests/docs/test_production_readiness_docs.py`: lock docs/readiness.
- Modify `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`: append Phase 61.

## Task 1: RED Client Tests

- [ ] Add `test_a2a_operation_client_task_resubscribe_posts_json_rpc`.
- [ ] Add `test_a2a_operation_client_task_resubscribe_rejects_required_unsupported_extension_before_network`.
- [ ] Add egress policy test for `task_resubscribe`.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_a2a_egress_url_policy.py -q
```

Expected: fails because `A2AOperationClient.task_resubscribe` is missing.

## Task 2: GREEN Client Implementation

- [ ] Implement `A2AOperationClient.task_resubscribe(...)`.
- [ ] POST to `{card.url}/tasks/{task_id}:subscribe`.
- [ ] Build payload with `A2AOperationRequest.task_resubscribe(...)`.
- [ ] Reuse `_validate_url(...)` and `_headers_for_card(...)`.
- [ ] Parse response via `a2a_operation_response_from_dict(...)`.
- [ ] Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\channels\test_a2a_egress_url_policy.py -q
```

Expected: all selected tests pass.

## Task 3: Docs And Readiness

- [ ] Add readiness evidence for `A2AOperationClient.task_resubscribe`.
- [ ] Update production docs and agent-os skill modules.
- [ ] Add doc/readiness assertions.
- [ ] Append Phase 61 to the roadmap.
- [ ] Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: all pass.

## Task 4: Verification

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
