# A2A OIDC Claims Auth Boundary Design

> Date: 2026-06-15
> Phase: 47
> Branch: `review/agentos-sdk-architecture-20260611`

## Target Conclusion

A2A peer trust cannot stay at static bearer-token comparisons once agents are
registered across services or tenants. The SDK should provide a narrow JWT/OIDC
claims verification boundary for inbound A2A calls: verify signature, issuer,
audience, time validity, and optional peer allow-list before an operation runs.
Full OIDC discovery, RS256/JWKS public-key rollout, CA policy, DNS pinning,
egress proxying, and tenant RBAC remain deployment-owned.

## External Baseline

OIDC ID tokens and OAuth access tokens rely on JWT claims:

- `iss` identifies the issuer and must match the expected issuer exactly.
- `aud` identifies intended recipients and must include the current service
  audience.
- `exp` rejects expired tokens.
- `nbf` rejects tokens not yet valid.
- Implementations often allow a small clock-skew leeway.

A2A operation servers already accept an `A2AInboundAuthPolicy`. This phase adds
a stronger policy behind that existing boundary rather than changing operation
server routing or runtime loops.

## SDK Boundary

Add JWT/OIDC claims auth primitives in `agentos.channels.a2a`:

- `A2AJwtClaims`: stable claim projection exposed to authorization logic.
- `A2AJwtVerifier`: protocol for token verifiers.
- `HmacA2AJwtVerifier`: minimal HS256 verifier for local/dev and deployments
  that intentionally use shared-secret JWTs.
- `OidcClaimsA2AInboundAuthPolicy`: inbound auth policy that extracts bearer
  tokens, verifies claims, enforces issuer/audience/time validity, and applies
  optional peer allow-lists.

The policy must raise `A2AInboundAuthError` with generic messages and must not
leak tokens or secrets through `repr`.

## Non-Goals

- No OIDC discovery document fetch.
- No RS256/ES256/JWKS public-key JWT verification.
- No CA trust rollout policy.
- No tenant RBAC mapping beyond optional peer-id allow-lists.
- No DNS pinning or egress proxy.
- No coupling to `QueryLoop`, `AsyncQueryLoop`, planner, or team runtime.

## Acceptance Criteria

- Valid HS256 JWT bearer tokens pass inbound A2A operation auth when issuer,
  audience, and time claims match.
- Wrong issuer, wrong audience, expired token, not-yet-valid token, and
  disallowed peer ids are rejected before operation execution.
- `repr(...)` redacts secrets and does not include bearer tokens.
- Public exports include the verifier, claims, and policy types.
- Readiness/docs/skill guidance list JWT/OIDC claims validation as available
  while keeping OIDC discovery, RS256/JWKS, CA, DNS/egress, credential rotation,
  and tenant RBAC as remaining production governance work.
- Runtime boundary scan finds no A2A/auth coupling in query loops.
