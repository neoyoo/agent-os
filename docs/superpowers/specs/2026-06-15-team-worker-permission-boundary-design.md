# Team Worker Permission Boundary Design (Phase 18A)

> Date: 2026-06-15
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 13A worker sessions, Phase 14A runner, Phase 15A daemon, Phase 16A/16B retry, Phase 17A cancellation

## Target Conclusion

```text
Team workers are not full-power copies of the leader. The SDK should enforce a
permission downgrade boundary when worker sessions are created, at minimum
blocking workers from receiving a workspace broader than the team workspace or
one whose local root escapes the team root.
```

## Problem

`TeamWorkerSessionProvider` turns a team member into an independent worker
session, but the current in-memory provider accepts `requested_workspace` as-is.
That leaves production profiles to remember to check every `agent_create`
request before the session is registered.

For a team discussion SDK primitive, this is too soft. The worker session
boundary should reject unsafe workspace requests before `TeamRuntime` stores the
member and before a runner can execute worker continuations.

## Scope

Phase 18A adds:

- `TeamWorkerPermissionPolicy`
- `TeamWorkerPermissionError`
- workspace resolution inside `InMemoryTeamWorkerSessionProvider`
- capability allow-list enforcement when a policy provides one
- public exports, readiness matrix, SDK skill guidance, and roadmap updates

Out of scope:

- OS/container sandboxing
- tool backend path rewriting
- persistent permission policy storage
- A2A trust/auth policy
- UI stream protocol
- persistent cancellation storage

## Semantics

Workspace rules:

- If no worker workspace is requested, the worker receives the team workspace.
- If neither team nor requested workspace exists, the session can still be
  created for local/prototype flows.
- If a worker workspace is requested without a team workspace, the request is
  rejected because the provider cannot prove it is narrowed.
- A worker workspace cannot have a broader `WorkspaceScope` than the team
  workspace.
- If both workspaces expose local roots, the worker root must equal or sit under
  the team root.
- If the team workspace has a local root and the requested worker workspace does
  not, the request is rejected because the local boundary cannot be verified.

Capability rules:

- By default, the policy does not reinterpret capability labels.
- A production profile can pass `allowed_capabilities`; any worker capability
  outside that set is rejected during session creation.
- This is a capability declaration boundary, not tool sandbox enforcement.

## Acceptance Criteria

- Unsafe broader worker scope is rejected before a worker session is stored.
- Worker local roots outside the team root are rejected.
- Missing requested workspace defaults to the team workspace without widening.
- Optional capability allow-list rejects undeclared worker capabilities.
- `TeamRuntime.add_member()` does not persist the member when worker session
  permission checks fail.
- Public API exports the new policy and error types.
- Readiness and skill docs move permission downgrade from pure app-owned gap to
  SDK primitive, while still warning that tool sandbox enforcement is app-owned.
- Runtime loop files do not import team permission policy types.
