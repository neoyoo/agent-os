# A2A Per-Peer Rate Limit Boundary Design

## Phase Target Conclusion

Public A2A services cannot rely only on the global ASGI rate limiter. The SDK should provide an operation-layer rate-limit boundary that runs after peer authorization and before A2A runner/store work, keyed by authenticated peer, operation, task, and resource context. Cluster-wide quota storage, billing tiers, gateway enforcement, and Redis-backed global counters remain deployment-owned.

## Scope

This phase adds a narrow SDK primitive for inbound A2A operation throttling:

- A protocol for resolving peer identity from already-authenticated request headers.
- A protocol for A2A operation rate-limit policies.
- A default peer-keyed operation policy backed by the existing channel `RateLimiter`.
- Structured A2A error mapping for rate-limit denial.
- `A2AOperationServer` integration after version/extension/auth checks and before executing a runner, task lifecycle, or push notification config store.

This phase does not add:

- Distributed Redis or gateway-level counters.
- Business quota plans, tenant billing, or commercial entitlement logic.
- IP reputation, bot detection, or WAF policy.
- Runtime-loop awareness of A2A, peer identity, or rate limiting.

## Architecture

The new boundary lives in `agentos.channels.a2a_operations` because it governs protocol operation execution. It consumes the existing `agentos.channels.rate_limit.RateLimiter` protocol rather than creating another counter abstraction.

The default policy is intentionally compositional:

```text
A2AOperationServer
  -> version policy
  -> extension negotiation policy
  -> inbound auth policy
  -> A2A operation rate-limit policy
  -> runner / task lifecycle / push config store
```

Peer identity remains separate from authorization return values. Existing auth policies continue to raise or allow. A peer-id resolver is a small injected object with `peer_id_for_headers(headers) -> str | None`, so deployments can reuse bearer-token maps, OIDC claims, gateway identity headers, or custom tenancy logic without changing the auth protocol.

## Public API

- `A2APeerIdResolver`
- `A2AOperationRateLimitPolicy`
- `A2ARateLimitError`
- `PeerKeyA2AOperationRateLimitPolicy`

`PeerKeyA2AOperationRateLimitPolicy` builds stable keys from:

- peer id
- operation name
- task id, when present
- resource type and resource id, when present

The key shape is SDK-internal and stable enough for tests, but callers should not rely on it for billing or analytics.

## Error Mapping

Rate-limit denial returns a JSON-RPC-like A2A error:

```json
{
  "code": -32029,
  "message": "rate limit exceeded",
  "data": {
    "type": "https://a2a-protocol.org/errors/rate-limit-exceeded",
    "title": "Rate Limit Exceeded",
    "status": 429,
    "retryAfterSeconds": 60
  }
}
```

The response must not expose bearer tokens, raw headers, or internal limiter keys.

## Acceptance Criteria

- `message/send` requests from one peer are denied before the runner after the peer exceeds its configured operation bucket.
- Another peer with a different peer id uses a different bucket.
- Different operations for the same peer use different buckets.
- Task/resource operations include task and resource context in the rate-limit key.
- Public API exports are available from `agentos.channels` and top-level `agentos`.
- Readiness/docs/skill guidance no longer says per-peer A2A rate policy is entirely app-owned; it must say SDK owns the local operation boundary while distributed/global quota remains deployment-owned.
- `src/agentos/runtime/query_loop.py` and `src/agentos/runtime/async_query_loop.py` remain free of A2A/rate-limit/planner/team concepts.
