# A2A Protocol Version Boundary Implementation Plan

Spec: `docs/superpowers/specs/2026-06-15-a2a-protocol-version-boundary-design.md`

## Target Conclusion

A2A operation calls should carry an explicit protocol-version contract. The SDK
owns the request/response version boundary; full A2A payload parity and
extension negotiation remain later phases.

## Tasks

1. Add failing A2A operation tests for outbound `A2A-Version`, explicit header
   override, inbound unsupported-version rejection, missing-version default
   handling, and public API exports.
2. Implement `A2AProtocolVersionPolicy` with `Major.Minor` normalization and
   `VersionNotSupportedError` response helpers in `a2a_operations.py`.
3. Wire `A2AOperationClient` to send the default version header and
   `A2AOperationServer` to validate inbound versions before operation work.
4. Export the version policy through `agentos.channels` and top-level
   `agentos`.
5. Update readiness, production docs, SDK guidance, and the architecture
   roadmap Phase 41 entry.
6. Verify targeted A2A/readiness/public API tests, full tests, compileall,
   runtime boundary scan, and diff hygiene.

## Verification Commands

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\architecture\test_public_api.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```
