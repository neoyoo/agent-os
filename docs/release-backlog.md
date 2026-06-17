# AgentOS RC P2 Release Backlog

This file records P2 items that should not block the current release-candidate
review when the SDK contract is honest, tested, and documented. Items here are
not waived permanently; they are explicit follow-up work.

## Planner dispatch crash window

Status: backlog, non-blocking for RC.

This entry tracks the planner dispatch crash window.

Risk: `PlannerRuntime.assign_step(...)` saves a step assignment before calling
the external coordinator. If the process crashes between plan mutation and
coordinator spawn, the step can remain `assigned` without a worker process or
task submission evidence. Existing exception handling records coordinator
failures, but a process crash in that gap cannot be caught by normal Python
error handling.

Required follow-up: add a dispatch outbox or pending-dispatch marker before the
external coordinator boundary, then add a compensation scanner that can either
submit the pending assignment idempotently or mark it failed with recovery
evidence. The scanner should run under deployment-owned process supervision and
use the `PlanStore` truth source.

Mitigation for this RC: use `PlannerRuntime.claimed_scheduler_tick(...)` with a
`PlanClaimStore`, run planner workers under a supervisor, and monitor
`PlannerWorkerDispatchSupervisionProfile` plus stale claim sweep evidence. This
is non-blocking for RC because the current release has PlanStore public boundary
tests, focused planner behavior tests for save-before-dispatch and coordinator
failure handling, and no known P1 behavior bug in normal coordinator failure or
claim-guarded scheduler paths.

## A2A public operation rate limiting

Status: production guidance strengthened, distributed quota backlog.

Production A2A reference services should pass
`A2AOperationServer(rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy(...))`
when exposing public A2A operations. The SDK owns the local per-peer operation
rate-limit boundary through `A2AOperationRateLimitPolicy`,
`PeerKeyA2AOperationRateLimitPolicy`, `A2APeerIdResolver`, and
`A2ARateLimitError`. The generic constructor shape is
`A2AOperationServer(rate_limit_policy=...)`.

Required follow-up: distributed/global quota storage, gateway enforcement,
commercial entitlement policy, and billing tiers remain deployment-owned.

## Team worker capability allow-list

Status: production guidance strengthened and tool-path test coverage added.

Production team runtimes that expose `agent_create` must pair worker session
creation with `TeamWorkerPermissionPolicy(allowed_capabilities=...)`, narrowed
workspace handles, and worker `WorkspaceToolSandboxPolicy` instances. This
keeps worker capabilities from broadening at the session boundary before
external tool handlers run.

Required follow-up: production profiles should wire durable worker session
providers to the deployment session system and process supervisor.

## Large-module decomposition

Status: backlog, non-blocking for RC.

This entry tracks large-module decomposition.

Large modules increase maintenance risk and should be split after the RC review:

- `src/agentos/channels/a2a_operations.py`
- `src/agentos/multi/planner.py`
- `src/agentos/multi/team.py`
- `src/agentos/channels/asgi.py`

Recommended split:

- Move A2A auth/rate-limit/push/conformance operation helpers into focused
  modules with stable re-exports.
- Split planner profiles, stores, tools, daemon loops, and serialization
  helpers while preserving `agentos.multi` public imports.
- Split team records/stores, worker lifecycle, UI streams, and tools while
  preserving `agentos.multi.team` import compatibility.
- Split ASGI A2A, team UI, durable session, and core HTTP/SSE routing helpers.

This is non-blocking for RC because public boundary tests cover the stable API
surface, focused behavior tests cover the high-risk planner/team/A2A paths, and
there is no known P1 behavior bug caused by module size alone.
