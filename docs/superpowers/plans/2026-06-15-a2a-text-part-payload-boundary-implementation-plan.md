# A2A Text Part Payload Boundary Implementation Plan

Spec: `docs/superpowers/specs/2026-06-15-a2a-text-part-payload-boundary-design.md`

## Target Conclusion

A2A 1.0 operation payloads should no longer emit the legacy `kind` discriminator
for text parts. The SDK owns text-part payload shape compatibility while full
file/data/artifact/event parity stays in later phases.

## Tasks

1. Add failing serializer/client tests for `{"text": "..."}` output and legacy
   `{"kind": "text", "text": "..."}` input compatibility.
2. Implement the minimal serializer/deserializer change in
   `a2a_operations.py`.
3. Update readiness, production docs, SDK guidance, and roadmap Phase 42 entry.
4. Run targeted A2A/readiness/public API tests, full tests, compileall,
   runtime boundary scan, and diff hygiene.

## Verification Commands

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```
