# Release Hardening Implementation Plan

## Target Conclusion

Phase 100: Release Hardening turns the branch into a release candidate by
making release governance explicit and testable. It does not expand runtime
behavior.

## Steps

1. Add RED docs tests for release hardening gate evidence.
2. Add `docs/release-hardening.md` with release candidate evidence and commands.
3. Add `docs/api-stability.md` with stable API and experimental API
   classification.
4. Add `docs/migrations/README.md` as the migration index.
5. Add `CHANGELOG.md` with Phase 96 through Phase 100 entries.
6. Align `README.md`, `docs/quickstart.md`, production readiness, objective
   audit, roadmap, and agent-os skill guidance.
7. Verify targeted docs tests, public API audit, compileall, diff check,
   runtime boundary scan, and full test suite.

## Verification

```bash
uv run pytest tests/docs/test_production_hardening_docs.py tests/docs/test_production_readiness_docs.py tests/docs/test_objective_coverage_audit_docs.py -q
uv run pytest tests/architecture/test_public_api.py -q
uv run python -m compileall -q src tests
git diff --check
rg -n "ReferenceLiveBackendProbe|REFERENCE_LIVE_BACKEND|ReferenceStatePlane|state plane|readiness|planner|team|A2A|sandbox|worker supervisor|production_design_constraints|release hardening" src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py
uv run pytest -q
```

