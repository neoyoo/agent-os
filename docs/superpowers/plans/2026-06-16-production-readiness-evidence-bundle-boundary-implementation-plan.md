# Production Readiness Evidence Bundle Boundary Implementation Plan

## Target Conclusion

AgentOS should close the release gate evidence boundary by adding a JSON-safe
bundle over existing readiness/profile/backend evidence, documenting the
sdk_owned/deployment_owned split, and keeping real infrastructure checks outside
the SDK.

## Tasks

- [x] Add `ReadinessEvidenceStatus`.
- [x] Add `ReadinessEvidenceCheck`.
- [x] Add `ProductionReadinessEvidenceBundle`.
- [x] Normalize mapping, callable, `readiness_check`, `readiness_metadata`, and
  `as_dict` evidence sources.
- [x] Report `accepted`, `blocking_checks`, `missing_required_checks`, and
  `block_production_readiness`.
- [x] Redact secret-like evidence and metadata keys.
- [x] Export the new API from `agentos.readiness` and top-level `agentos`.
- [x] Add tests for required checks, blocking checks, JSON-safe payloads,
  callable source invocation, public exports, and documentation guidance.
- [ ] Update production readiness docs, objective coverage audit, roadmap, and
  agent-os skill guidance.
- [ ] Run targeted tests, compileall, diff check, boundary scan, and full test
  suite.

## Verification

Run:

```powershell
uv run pytest tests\test_readiness.py tests\architecture\test_public_api.py::test_readiness_public_api_exports tests\docs\test_production_readiness_docs.py::test_production_readiness_doc_describes_readiness_evidence_bundle_boundary tests\docs\test_objective_coverage_audit_docs.py::test_objective_coverage_audit_names_evidence_and_remaining_blockers tests\docs\test_objective_coverage_audit_docs.py::test_production_readiness_and_roadmap_link_objective_audit -q
uv run python -m compileall -q src tests
git diff --check
rg -n "ProductionReadinessEvidenceBundle|ReadinessEvidence" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
uv run pytest -q
```

The runtime boundary scan should find no planner/A2A/team/worker/sandbox or
readiness bundle concepts inside `QueryLoop` or `AsyncQueryLoop`.

