# A2A Stream Lifecycle Profile Boundary Design

> Date: 2026-06-16
> Phase: 63

## Target Conclusion

A2A streaming and task resubscribe are now composed from narrow SDK operation
boundaries, but production deployments still need an explicit stream lifecycle
contract. The SDK should expose a JSON-safe deployment profile that names the
required lifecycle components and deployment-owned responsibilities for
reconnect policy, durable cursor storage, fan-out, backpressure, quota, billing,
credential issuance, supervision, egress governance, CA rollout, tenant
directory lifecycle, and external conformance execution. It must not implement
those loops or storage systems inside `QueryLoop`, `AsyncQueryLoop`, or the
A2A operation client.

## Problem

Docs currently repeat that reconnect, durable cursor storage, fan-out, and
backpressure remain deployment-owned, but there is no SDK type that can be
attached to readiness metadata or deployment probes to make that boundary
machine-readable. As A2A support grows, users need a stable way to distinguish
"SDK protocol primitives are ready" from "this deployment has provided the
production stream lifecycle services around those primitives."

## Scope

In scope:

- Add `A2AStreamLifecycleDeploymentProfile` as a small dataclass in
  `src/agentos/channels/a2a_operations.py`.
- Provide `readiness_metadata()` with:
  - `profile`
  - `probe_name`
  - `sdk_owned`
  - `required_components`
  - `configured_components`
  - `missing_components`
  - `deployment_owned`
  - `ready`
- Provide `readiness_check()` returning an ASGI readiness-compatible payload.
- Validate non-empty probe names and configured component names.
- Export the profile from `agentos.channels` and `agentos`.
- Add readiness/docs/skill/roadmap evidence.

Out of scope:

- Implementing reconnect loops.
- Implementing durable cursor storage.
- Implementing SSE fan-out or backpressure queues.
- Implementing gateway/global quota storage, billing, credential issuance,
  process supervision, DNS pinning, CA rollout, tenant directory lifecycle, or
  external conformance suite execution.

## Required Components

The default required components are:

- `reconnect_policy`
- `durable_cursor_store`
- `fanout_broker`
- `backpressure_policy`
- `stream_supervision`

These names are deployment contract labels, not SDK adapters. A deployment can
mark a component configured when it has provided an app/profile-owned service
or gateway that satisfies the responsibility.

## Acceptance

- `A2AStreamLifecycleDeploymentProfile().readiness_metadata()` reports all
  default required components as missing and `ready` false.
- Passing all required component names as configured returns `ready` true and
  an `ok` readiness check.
- Partial configuration returns a failed readiness check with missing component
  names.
- Public API tests prove the profile is exported from `agentos.channels` and
  `agentos`.
- Readiness and docs name the profile while preserving deployment ownership of
  reconnect loops, durable cursor storage, fan-out, backpressure, gateway quota,
  billing, credential issuance, supervision, egress governance, CA rollout,
  tenant directory lifecycle, and external conformance execution.
