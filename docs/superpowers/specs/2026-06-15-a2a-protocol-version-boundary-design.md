# A2A Protocol Version Boundary Design

Date: 2026-06-15

## Target Conclusion

A2A interoperability cannot rely only on matching payload shapes. The SDK
should expose a narrow protocol-version boundary that sends `A2A-Version` on
outbound operation requests, validates inbound requested versions, and returns
the A2A `VersionNotSupportedError` shape when a peer asks for an unsupported
version. Full A2A conformance, payload-model migration, and extension
negotiation remain separate phases.

## External Baseline

The A2A 1.0 specification identifies the protocol version by `Major.Minor`
values, for example `1.0`. Clients must send the `A2A-Version` header with each
request, servers must treat an empty value as `0.3`, and unsupported versions
map to `VersionNotSupportedError` with JSON-RPC error code `-32009`.

## Design

- Add a small `A2AProtocolVersionPolicy` value object.
- Default client version to `1.0` for protocol operation requests.
- Include `A2A-Version` in `A2AOperationClient` requests unless the caller
  explicitly provides an override header.
- Let `A2AOperationServer` accept a configured set of supported versions.
- Validate protocol version before auth and operation execution for
  operation, task lifecycle, and push notification config routes.
- Normalize patch versions such as `1.0.0` to `1.0` at the policy boundary.
- Preserve the legacy A2A rule that a missing or empty version means `0.3`.
- Keep version negotiation inside `agentos.channels.a2a_operations`; do not
  leak A2A concepts into QueryLoop, AsyncQueryLoop, planner, or team runtime.

## Non-Goals

- No automatic fallback between incompatible protocol versions.
- No migration of the current message part serializer from the existing
  `kind` form to the A2A 1.0 wrapper-object form.
- No complete conformance harness in this slice.
- No `A2A-Extensions` negotiation.
- No extended authenticated Agent Card endpoint.

## Acceptance Criteria

- `A2AOperationClient` sends `A2A-Version: 1.0` by default.
- Explicit caller headers can override the default client version.
- `A2AOperationServer` accepts supported versions and rejects unsupported
  versions with error code `-32009`.
- Missing or empty inbound version is interpreted as `0.3`.
- Public API exports expose the version policy.
- Readiness and SDK guidance describe protocol version negotiation as an
  SDK primitive while keeping full conformance as roadmap work.
