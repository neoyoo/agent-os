# Workspace Execution Isolation Profile Boundary Design

## Target Conclusion

Workspace-aware agents need a production-facing isolation contract that does not
confuse SDK pre-execution checks with real process/container sandboxing. The SDK
should expose a deployment profile that states which workspace isolation
components are configured, which protections are SDK-owned, and which remain
deployment-owned.

## Current State

The SDK already owns:

- `WorkspaceHandle`
- `WorkspaceProvider`
- `WorkspaceRequest`
- `WorkspacePolicy`
- `LocalWorkspaceProvider`
- workspace scope narrowing
- `WorkspaceToolSandboxPolicy`
- `ToolPathSandboxRule`
- tool capability pre-checks
- path escape rejection before handler execution

These are useful pre-execution controls, but they are not a secure execution
environment for untrusted code. Production deployments still need process or
container isolation, resource limits, network egress policy, filesystem mount
policy, secret redaction, audit logging, and live sandbox backend verification.

## Proposed Boundary

Add `WorkspaceExecutionIsolationProfile` in `agentos.workspace`.

The profile:

- accepts configured deployment component names
- reports missing required isolation components
- exposes `readiness_metadata()`
- exposes ASGI-compatible `readiness_check()`
- returns JSON-safe tuples/booleans/strings
- documents SDK-owned and deployment-owned responsibilities

Required components:

- `workspace_policy`
- `tool_path_sandbox`
- `capability_allowlist`
- `execution_backend`
- `process_isolation`
- `resource_limits`
- `network_policy`
- `audit_logging`

SDK-owned responsibilities:

- `WorkspaceHandle`
- `WorkspaceProvider`
- `WorkspacePolicy`
- `scope narrowing`
- `WorkspaceToolSandboxPolicy`
- `ToolPathSandboxRule`
- `path escape pre-check`
- `tool capability pre-check`

Deployment-owned responsibilities:

- `OS/container sandboxing`
- `process isolation`
- `filesystem mount policy`
- `network egress policy`
- `CPU and memory limits`
- `secret redaction`
- `audit logging backend`
- `sandbox image/runtime patching`
- `live sandbox backend verification`

## Non-Goals

- No container runtime.
- No process launcher.
- No seccomp/AppArmor policy generation.
- No filesystem mount management.
- No network proxy.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Validation

- Workspace tests prove missing/ready metadata.
- Public API tests prove exports from `agentos.workspace` and top-level
  `agentos`.
- Readiness/docs tests prove terminal/web/team forms do not over-claim
  OS/container sandboxing.
- Runtime boundary scan proves workspace isolation concepts do not leak into
  query loops.
