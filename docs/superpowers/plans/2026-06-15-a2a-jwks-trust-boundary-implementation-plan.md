# A2A JWKS Trust Boundary Implementation Plan

Spec: `docs/superpowers/specs/2026-06-15-a2a-jwks-trust-boundary-design.md`

## Target Conclusion

A2A card trust should support configured JWKS key discovery without turning
agent-os into an identity provider or coupling trust validation into QueryLoop.

## Tasks

1. Add failing A2A card tests for JWKS-backed HMAC verification, caching,
   HTTPS-only URLs, unsupported keys, key-id allow-lists, and resolver
   integration.
2. Implement `JwksA2ACardTrustStore` behind the existing
   `A2ACardTrustStore` protocol.
3. Export the new trust store through `agentos.channels` and top-level
   `agentos`.
4. Update readiness, production docs, and SDK skill guidance so JWKS key
   discovery is no longer listed as entirely missing.
5. Verify targeted A2A/readiness/public API tests, full test suite, compileall,
   runtime boundary scan, and diff hygiene.

## Verification Commands

```powershell
uv run pytest tests\channels\test_a2a_card.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\architecture\test_public_api.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```
