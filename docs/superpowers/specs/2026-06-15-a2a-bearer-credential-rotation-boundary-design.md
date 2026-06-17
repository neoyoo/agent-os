# A2A Bearer Credential Rotation Boundary Design

## Target Conclusion

A2A production deployments should be able to rotate bearer credentials without
rewriting every auth provider or inbound policy. The SDK should provide a narrow
credential rotation boundary that selects one current outbound bearer token,
accepts active overlapping inbound bearer tokens, rejects expired, not-yet-valid,
or revoked credentials, and redacts secrets from diagnostics. Secret generation,
KMS or secret-manager distribution, approval workflow, audit policy, CA trust,
and DNS/egress governance remain deployment-owned.

## Scope

This phase adds SDK primitives for bearer-token rotation only:

- `A2ABearerCredential`: one bearer token with `key_id`, `peer_id`, activation
  window, and revocation flag.
- `RotatingBearerA2ACredentialStore`: in-memory rotation boundary that finds
  active credentials, returns the current outbound credential, and maps inbound
  tokens to peer ids with constant-time comparison.
- `RotatingBearerA2AAuthProvider`: outbound `A2AAuthProvider` using the current
  active credential.
- `RotatingBearerA2AInboundAuthPolicy`: inbound auth policy that accepts active
  overlap credentials, optionally restricts peers, and can enforce operation and
  resource maps.

The phase does not add persistent credential storage or remote secret refresh.
Deployments can rebuild the store from their own KMS, secret manager, config
reload, or process rollout mechanism.

## Architecture

The boundary lives in `src/agentos/channels/a2a.py` because it is an A2A channel
auth concern, not a runtime-loop concern. It follows the existing
`RotatingA2ACardTrustStore` pattern while keeping bearer credentials separate
from Agent Card signing keys.

Data flow:

```text
outbound A2A call
  -> RotatingBearerA2AAuthProvider.headers_for_card(...)
  -> RotatingBearerA2ACredentialStore.current_credential()
  -> Authorization: Bearer <current active token>

inbound A2A operation
  -> RotatingBearerA2AInboundAuthPolicy.authorize*()
  -> parse Authorization bearer token
  -> RotatingBearerA2ACredentialStore.peer_id_for_token(...)
  -> optional peer / operation / resource checks
```

## Security Properties

- Tokens are never included in `repr(...)`.
- Inbound token matching uses `hmac.compare_digest`.
- Expired, future, revoked, missing, malformed, or unknown tokens all fail with
  `A2AInboundAuthError("unauthorized peer")`.
- Current outbound credential must be active; otherwise the provider raises a
  typed `A2ACredentialRotationError`.
- Overlap windows are explicit through `not_before` and `not_after`.

## Acceptance Criteria

- Outbound auth uses only the configured current active bearer credential.
- Inbound auth accepts old and new credentials during overlap.
- Expired, not-yet-valid, revoked, missing, and unknown credentials are rejected
  with a generic unauthorized error.
- Optional peer, operation, task id, and resource allow-lists work with rotated
  credentials.
- New public names are exported from `agentos.channels` and top-level
  `agentos`.
- `agentos.readiness`, `docs/production-readiness.md`, agent-os skill docs, and
  the architecture review roadmap describe credential rotation as an SDK
  boundary while keeping secret distribution and governance deployment-owned.
- `src/agentos/runtime/query_loop.py` and
  `src/agentos/runtime/async_query_loop.py` remain free of A2A/auth concepts.
