# Sandbox / Workspace Backend Boundary Design

## Target Conclusion

Production agents need a pluggable execution boundary for local tools, generated
subagent work, and future Docker/E2B/enterprise runners. AgentOS should define a
stable workspace execution backend protocol and a local reference adapter, while
leaving real isolation kernels, container images, remote sandbox operations,
network policy, and patching to deployment-owned backends.

This follows the direction seen in AgentScope 2.0, which advertises workspace
and sandbox backends for local, Docker, and E2B execution, and E2B's model where
an external sandbox service owns isolated filesystem and command/code execution.

## Current State

The SDK already has:

- `WorkspaceHandle`, `WorkspaceRequest`, `WorkspaceProvider`
- `LocalWorkspaceProvider`
- `WorkspacePolicy` and scope narrowing
- `WorkspaceToolSandboxPolicy` and `ToolPathSandboxRule`
- `WorkspaceExecutionIsolationProfile`
- generic `ExecutionBackend` for in-process tool handler execution

The missing layer is a workspace-aware backend protocol that can run a command
or delegated unit of work inside a workspace and return JSON-safe audit
evidence. Existing tool execution can continue to use `ExecutionBackend`; this
phase adds a separate workspace/sandbox backend boundary for process or remote
sandbox execution.

## Proposed Boundary

Add the following primitives in `agentos.workspace`:

- `WorkspaceExecutionRequest`: argv-only command request tied to a
  `WorkspaceHandle`, capability, optional cwd, timeout, env, and metadata.
- `WorkspaceExecutionResult`: stdout/stderr result plus JSON-safe evidence.
  Evidence includes command, workspace identity, capability, cwd, exit code,
  timestamps, timeout/error state, output sizes, and env keys only.
- `WorkspaceExecutionPolicy`: SDK pre-check for workspace root, cwd path escape,
  and capability allow-list.
- `WorkspaceExecutionBackend`: protocol for sync and async execution.
- `SandboxBackend`: protocol alias shape for Docker/E2B/enterprise sandbox
  adapters.
- `LocalWorkspaceExecutionBackend`: reference adapter using `subprocess.run`
  with `shell=False`, argv tuples, workspace-root cwd enforcement, captured
  stdout/stderr, timeout handling, and JSON-safe evidence.

## SDK-Owned

- Stable request/result/protocol types
- Local reference backend
- argv-only command contract
- workspace root/cwd pre-check
- capability allow-list
- environment value redaction in evidence
- readiness and skill guidance

## Deployment-Owned

- Docker/E2B/enterprise runner implementations
- container/microVM isolation
- filesystem mount policy
- network egress policy
- CPU/memory enforcement
- secret injection and redaction beyond env-key evidence
- sandbox image/runtime patching
- live sandbox backend verification
- production audit log storage

## Non-Goals

- No Docker adapter in this phase.
- No E2B adapter in this phase.
- No shell command string parsing.
- No OS/container security claim for the local adapter.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Validation

- Workspace tests prove policy rejection, local backend success, timeout
  handling, and env value redaction.
- Public API tests prove exports.
- Readiness/docs tests prove the backend boundary is documented without
  claiming production isolation.
- Runtime boundary scans prove sandbox execution concepts do not leak into query
  loops.
