# Planner Step Recovery Implementation Plan

Spec: `docs/superpowers/specs/2026-06-15-planner-step-recovery-design.md`

## Target Conclusion

Planner should have an SDK-owned, auditable failure/retry state boundary, while
dispatch scheduling and compensation remain app/profile-owned.

## Tasks

1. Add failing runtime tests for `PlanRetryPolicy`, `fail_step`,
   `retryable_steps`, `retry_step`, and retry exhaustion.
2. Add failing tool tests for `plan_fail_step`, `plan_retryable_steps`, and
   `plan_retry_step`, including owner isolation.
3. Add failing serializer/Postgres and projection tests for retry metadata.
4. Implement minimal planner runtime and tool behavior.
5. Export `PlanRetryPolicy` through `agentos.multi` and top-level `agentos`.
6. Update readiness and SDK skill docs to reflect the new boundary.
7. Verify targeted planner/readiness tests, full test suite, compileall,
   runtime boundary scan, and diff hygiene.

## Verification Commands

```powershell
uv run pytest tests\multi\test_planner_runtime.py tests\multi\test_planner_tools.py tests\multi\test_planner_projection.py tests\multi\test_postgres_plan_store.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\architecture\test_public_api.py -q
uv run pytest -q
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```
