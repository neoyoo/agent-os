# A2A RS256 JWKS JWT Verifier Design

> Date: 2026-06-15
> Phase: 49
> Branch: `review/agentos-sdk-architecture-20260611`

## Target Conclusion

A2A OIDC peer auth should be able to verify public-key JWT signatures without
turning the runtime loop into an identity provider. The SDK should provide a
narrow RS256/JWKS JWT verifier that fetches trusted JWKS metadata, selects an
explicit `kid`, enforces `alg == RS256`, verifies the RSA SHA-256 signature, and
returns the existing `A2AJwtClaims` projection. OIDC discovery metadata can feed
the JWKS URL, while CA policy, DNS pinning, egress proxying, key rotation
governance, and tenant RBAC remain deployment/profile responsibilities.

## External Baseline

JWT/JWK production identity relies on three RFC boundaries:

- RFC 7519 defines JWT compact serialization and standard claims such as
  `iss`, `sub`, `aud`, `exp`, `nbf`, and `iat`.
- RFC 7517 defines JWK/JWKS key material, including RSA keys with `kty`, `kid`,
  `n`, `e`, `use`, and `alg` metadata.
- RFC 7518 defines `RS256` as RSASSA-PKCS1-v1_5 with SHA-256.

The SDK must not trust `alg` agility from an untrusted token. The verifier is
configured for RS256 only and rejects all other algorithms.

## SDK Boundary

Add RS256/JWKS JWT primitives in `agentos.channels.a2a`:

- `JwksA2AJwtVerifier`: an `A2AJwtVerifier` implementation that uses configured
  HTTPS JWKS URLs or an `OidcDiscoveryMetadataProvider`.
- JWKS cache with TTL and optional key-id allow-list.
- RSA public key material loaded from JWK `n` and `e` values.
- Signature verification through the optional `cryptography` package.

The verifier must:

- require HTTPS JWKS URLs when directly configured;
- fetch each JWKS through `A2ATransport.get_json(...)`;
- require JWKS payloads to be objects with a `keys` list;
- reject JWTs without `kid`, with unsupported `alg`, malformed segments, or
  unknown keys;
- ignore non-RSA, non-signing, unsupported-algorithm, malformed, or disallowed
  keys;
- cache usable public keys until TTL expiry;
- return `A2AJwtClaims` through the existing payload projection;
- raise `A2AInboundAuthError("unauthorized peer")` without leaking tokens or key
  material.

## Dependency Boundary

`cryptography` is an optional `security` extra, not a core dependency. If the
package is absent, constructing or using the verifier fails closed with a
generic authorization error or clear setup error; it must never silently accept
tokens.

## Non-Goals

- No ES256/ECDSA verifier.
- No WebFinger or dynamic issuer discovery beyond the Phase 48 metadata
  provider.
- No CA bundle policy, certificate pinning, DNS pinning, or egress proxy.
- No tenant RBAC mapping or per-claim authorization beyond existing
  `OidcClaimsA2AInboundAuthPolicy`.
- No coupling to `QueryLoop`, `AsyncQueryLoop`, planner, or team runtime.

## Acceptance Criteria

- A valid RS256 JWT signed by a JWKS RSA public key verifies and returns
  `A2AJwtClaims`.
- The verifier can obtain its JWKS URL from `OidcDiscoveryMetadataProvider`.
- Wrong signature, unknown `kid`, unsupported `alg`, non-RSA keys, and
  disallowed key ids fail closed.
- JWKS fetches are cached and refresh after TTL expiry.
- Public exports include `JwksA2AJwtVerifier`.
- `pyproject.toml` exposes `cryptography` through a `security` optional extra.
- Readiness/docs/skill guidance list RS256/JWKS JWT verification as available
  while keeping CA trust rollout, DNS/egress controls, credential rotation,
  tenant RBAC, and external conformance as remaining work.
- Runtime boundary scan finds no A2A/auth coupling in query loops.
