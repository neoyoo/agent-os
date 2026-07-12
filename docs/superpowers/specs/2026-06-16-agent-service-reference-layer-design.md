# Agent Service Reference Layer Design

## Target Conclusion

AgentOS needs a lightweight reference service layer that shows how to host a
production web agent without becoming a platform. The SDK should compose
`AsgiAgentApp`, `DistributedWebRuntimeProfile`, session snapshot persistence,
workspace execution backend references, auth/rate-limit hooks, and readiness
profiles into one auditable ASGI hosting reference while keeping gateways,
Kubernetes/systemd, tenant directory, secret distribution, autoscaling, CI/CD,
sandbox image patching, and live backend verification deployment-owned.

This closes the AgentScope-style "Agent Service" experience gap at the SDK
boundary: developers get a standard composition and readiness evidence, while
operators still choose and run the real infrastructure.

## Current State

The SDK already has the pieces:

- `AsgiAgentApp` exposes JSON/SSE turn routes plus `/health`, `/v1/health`,
  `/ready`, and `/v1/ready`.
- `DistributedWebRuntimeProfile` assembles `DurableAgentSessionProvider`,
  `SessionLeaseStore`, and `SessionPersistence` for multi-node session
  hydration.
- `DistributedWebSessionOperationsProfile` reports whether the deployment has
  lease, snapshot, migration, TTL, auth, workspace, credential, and live
  backend policy components.
- `ProductionStatePlaneDeploymentProfile` reports the registry/queue/truth
  store/worker/session state-plane split.
- `WorkspaceExecutionIsolationProfile`, `WorkspaceExecutionBackend`,
  `SandboxBackend`, and `LocalWorkspaceExecutionBackend` describe the workspace
  execution backend boundary.
- `ChannelAuthPolicy` and `RateLimiter` already plug into `AsgiAgentApp`.

The missing layer is a small SDK-owned reference composition that wires these
pieces together consistently and exposes one JSON-safe readiness payload.

## Proposed Boundary

Add a new public module `agentos.service` with:

- `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS`: stable required component
  names for a production web agent reference service.
- `AgentServiceReferenceProfile`: a readiness contract describing configured
  service-layer components, SDK-owned pieces, and deployment-owned gaps.
- `AgentServiceReference`: a composition object that builds an `AsgiAgentApp`
  from a `WebRuntimeProfile` or `DistributedWebRuntimeProfile`, injects
  auth/rate-limit hooks, and adds service, runtime, session, state-plane, and
  workspace readiness checks.

The reference service should:

- accept an existing runtime profile rather than constructing providers from
  credentials;
- call into `AsgiAgentApp` for HTTP behavior rather than defining new routes;
- expose `readiness_metadata()` and `readiness_check()` as JSON-safe evidence;
- include class names for the runtime profile, session provider, workspace
  backend, auth policy, and rate limiter;
- include child readiness payloads from distributed web session, state-plane,
  and workspace isolation profiles;
- redact secrets and never store credential values in evidence;
- keep `QueryLoop` and `AsyncQueryLoop` untouched.

## Required Components

The service readiness profile tracks:

- `asgi_agent_app`
- `runtime_profile`
- `session_provider`
- `snapshot_persistence`
- `workspace_execution_backend`
- `auth_policy`
- `rate_limiter`
- `readiness_endpoint`
- `health_endpoint`
- `state_plane_profile`
- `distributed_session_profile`
- `workspace_isolation_profile`
- `live_backend_verification`

The final item stays deployment-owned. The SDK can report whether the reference
configuration names it, but cannot execute live backend verification.

## SDK-Owned

- Reference composition object
- ASGI app construction from existing profile boundaries
- JSON-safe readiness metadata and checks
- Auth/rate-limit hook injection
- Workspace backend reference metadata
- State-plane/session/workspace readiness aggregation
- Public API exports and skill guidance

## Deployment-Owned

- Real provider credentials
- Redis/Postgres/Nacos credentials and migrations
- Kubernetes/systemd/service manager integration
- Gateway, TLS, CORS, WAF, tenant directory, and RBAC lifecycle
- Distributed/global rate-limit stores and billing
- Sandbox image/runtime patching and live sandbox verification
- Autoscaling, rollout, rollback, alerting, and runbooks
- Live backend verification execution

## Non-Goals

- No new HTTP router separate from `AsgiAgentApp`.
- No concrete Redis/Postgres/Nacos/E2B/Docker client construction.
- No gateway, tenant directory, secret manager, or CI integration.
- No production sandbox kernel implementation.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Validation

- Unit tests prove the reference service builds an `AsgiAgentApp`, wires
  readiness checks, and exposes `/ready` through the existing ASGI app.
- Unit tests prove auth and rate-limit hooks are injected and enforced by
  `AsgiAgentApp`.
- Unit tests prove readiness metadata is JSON-safe, includes child readiness
  payloads, and does not leak credentials.
- Public API tests prove `agentos.service` and top-level exports.
- Docs/readiness tests prove the Agent Service reference layer is documented
  as a reference implementation, not a platform.
- Runtime boundary scans prove service concepts do not leak into query loops.
