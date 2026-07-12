# A2A OIDC Discovery Boundary Design

> Date: 2026-06-15
> Phase: 48
> Branch: `review/agentos-sdk-architecture-20260611`

## Target Conclusion

A2A JWT claims validation should not require operators to hand-copy issuer
metadata forever. The SDK should own a narrow OIDC discovery metadata boundary
that fetches `/.well-known/openid-configuration`, requires exact issuer match,
requires an HTTPS `jwks_uri`, caches metadata, and exposes the JWKS URI to later
JWT verifier phases. RS256/JWKS JWT signature verification, CA policy, DNS
pinning, egress proxying, credential rotation, and tenant RBAC remain
deployment/profile work.

## External Baseline

OpenID Connect Discovery 1.0 defines an issuer discovery document under
`/.well-known/openid-configuration`. The returned metadata includes an `issuer`
identifier and a `jwks_uri` where signing keys can be found. The returned
`issuer` must match the issuer URL used by the relying party. TLS and
certificate validation are transport/deployment responsibilities.

## SDK Boundary

Add OIDC discovery primitives in `agentos.channels.a2a`:

- `A2AOidcDiscoveryError`: generic discovery metadata validation failure.
- `OidcDiscoveryMetadata`: stable projection with `issuer`, `jwks_uri`, and
  raw metadata.
- `OidcDiscoveryMetadataProvider`: fetches and validates issuer metadata through
  the existing `A2ATransport.get_json(...)` boundary.

The provider must:

- require an HTTPS issuer URL;
- build the discovery URL from `issuer.rstrip("/") +
  "/.well-known/openid-configuration"`;
- require the discovery response to be a JSON object;
- require `payload["issuer"]` to exactly match the normalized configured
  issuer;
- require `payload["jwks_uri"]` to be a non-empty HTTPS URL;
- cache metadata until the configured TTL expires;
- expose `metadata()` and `jwks_uri()` helpers.

## Non-Goals

- No RS256/JWKS JWT signature verifier.
- No integration into `OidcClaimsA2AInboundAuthPolicy` yet.
- No OIDC provider discovery over WebFinger.
- No CA trust rollout policy.
- No DNS pinning or egress proxy controls.
- No tenant RBAC mapping.
- No coupling to `QueryLoop`, `AsyncQueryLoop`, planner, or team runtime.

## Acceptance Criteria

- Metadata fetches use the expected well-known URL and timeout.
- Repeated calls before TTL expiry use cached metadata.
- Calls after TTL expiry refresh metadata.
- HTTP issuers, non-object payloads, mismatched issuers, and non-HTTPS
  `jwks_uri` values fail closed.
- Public exports include the discovery error, metadata, and provider types.
- Readiness/docs/skill guidance list OIDC discovery metadata as available while
  keeping RS256/JWKS JWT verification, CA trust, DNS/egress, credential
  rotation, tenant RBAC, and external conformance as remaining work.
- Runtime boundary scan finds no A2A/auth coupling in query loops.
