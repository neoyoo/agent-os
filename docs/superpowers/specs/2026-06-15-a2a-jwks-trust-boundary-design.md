# A2A JWKS Trust Boundary Design

Date: 2026-06-15

## Target Conclusion

A2A signed-card trust cannot depend only on locally embedded HMAC secrets. The
SDK should provide a narrowly scoped JWKS key-discovery boundary that composes
with the existing `A2ACardResolver` and `HmacA2ACardVerifier`, while OIDC issuer
validation, CA trust rollout, tenant RBAC, and full identity governance remain
deployment-owned.

## Design

- Add `JwksA2ACardTrustStore` implementing the existing
  `A2ACardTrustStore.secret_for_key_id(...)` boundary.
- Load JWKS from explicitly configured HTTPS URLs only. Do not trust arbitrary
  `jku` values embedded in untrusted cards.
- Support only `kty: "oct"` and `alg: "HS256"` in this SDK slice so the
  existing HMAC verifier can use discovered keys without adding crypto
  dependencies.
- Ignore keys whose `use` is not `sig`, whose algorithm is not `HS256`, or whose
  key id is outside an optional allow-list.
- Cache JWKS payloads with a configurable TTL to avoid network calls on every
  card verification.
- Keep outbound/inbound peer auth policies separate from card-signature trust.

## Non-Goals

- No RS256/ECDSA verification in this slice.
- No OIDC discovery or issuer/audience validation.
- No certificate authority rollout or pinning.
- No tenant RBAC mapping.
- No automatic trust in card-provided `jku` headers.

## Acceptance Criteria

- A card signed with an HMAC key published in trusted JWKS verifies through
  `HmacA2ACardVerifier`.
- JWKS fetches are cached.
- Non-HTTPS JWKS URLs are rejected.
- Non-signing, unsupported algorithm, and disallowed key ids are ignored.
- `A2ACardResolver(card_verifier=...)` can verify a remote card using the JWKS
  trust store.
- Public API exports expose `JwksA2ACardTrustStore`.
