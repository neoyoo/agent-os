# A2A Conformance Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned A2A self-conformance harness that returns structured pass/fail reports for the protocol surfaces already implemented by agent-os.

**Architecture:** Create a focused `agentos.channels.a2a_conformance` module that reuses existing A2A card, operation, artifact, event, version, and extension serializers. Keep the harness at the channel/protocol layer and export it through `agentos.channels` and top-level `agentos`.

**Tech Stack:** Python dataclasses, existing A2A serializers/policies, pytest.

---

Spec: `docs/superpowers/specs/2026-06-15-a2a-conformance-harness-design.md`

## Tasks

### Task 1: RED Tests For Passing Self-Conformance

**Files:**
- Create: `tests/channels/test_a2a_conformance.py`

- [ ] **Step 1: Write the failing test**

Add `test_a2a_conformance_report_passes_for_supported_card_and_payloads`.
The test imports `A2AConformanceHarness`, builds a card with one extension and
one skill, runs the harness, and asserts:

```python
assert report.passed is True
assert report.failed_checks == ()
assert "agent-card-required-fields" in report.check_ids
assert "operation-jsonrpc-envelope" in report.check_ids
assert "extension-negotiation" in report.check_ids
assert report.to_dict()["passed"] is True
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py::test_a2a_conformance_report_passes_for_supported_card_and_payloads -q
```

Expected: FAIL because `agentos.channels.a2a_conformance` does not exist.

### Task 2: RED Tests For Failed Findings

**Files:**
- Modify: `tests/channels/test_a2a_conformance.py`

- [ ] **Step 1: Write the failing test**

Add `test_a2a_conformance_report_fails_missing_card_fields_and_legacy_kind_payloads`.
The test builds a card with an empty URL/version and passes an explicit legacy
message payload part containing `kind`. It asserts:

```python
assert report.passed is False
assert "agent-card-required-fields" in {finding.check_id for finding in report.failed_checks}
assert "message-part-wrapper-shape" in {finding.check_id for finding in report.failed_checks}
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py::test_a2a_conformance_report_fails_missing_card_fields_and_legacy_kind_payloads -q
```

Expected: FAIL because the report API does not exist.

### Task 3: RED Test For Extension Error Mapping

**Files:**
- Modify: `tests/channels/test_a2a_conformance.py`

- [ ] **Step 1: Write the failing test**

Add `test_a2a_conformance_report_records_extension_negotiation_error_mapping`.
The test configures a required local extension and runs the harness without
declaring `A2A-Extensions`. It asserts the report contains a passing
`extension-required-error-mapping` check whose details include:

```python
{"errorCode": -32008, "missingExtensions": [required_uri]}
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py::test_a2a_conformance_report_records_extension_negotiation_error_mapping -q
```

Expected: FAIL because the harness does not exist.

### Task 4: Implement Minimal Harness

**Files:**
- Create: `src/agentos/channels/a2a_conformance.py`

- [ ] **Step 1: Add dataclasses**

Create:

```python
@dataclass(frozen=True, slots=True)
class A2AConformanceFinding:
    check_id: str
    title: str
    passed: bool
    detail: str = ""
    evidence: Mapping[str, object] = field(default_factory=dict)
```

Create `A2AConformanceCheck` for reusable check metadata and
`A2AConformanceReport` with `passed`, `failed_checks`, `check_ids`, and
`to_dict()`.

- [ ] **Step 2: Add `A2AConformanceHarness.run(...)`**

The method accepts `card`, optional payload samples, optional headers, optional
`A2AProtocolVersionPolicy`, and optional `A2AExtensionNegotiationPolicy`.
It accumulates findings and never stops at the first failure.

- [ ] **Step 3: Reuse existing serializers**

Use:

```python
a2a_card_to_dict
a2a_message_to_dict
a2a_artifact_to_dict
a2a_task_subscription_event_to_dict
a2a_task_artifact_update_event_to_dict
a2a_operation_request_to_dict
A2AProtocolVersionPolicy
A2AExtensionNegotiationPolicy
```

Do not duplicate protocol serialization logic.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_conformance.py -q
```

Expected: PASS.

### Task 5: Public API And Guidance

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Export harness types**

Export `A2AConformanceCheck`, `A2AConformanceFinding`,
`A2AConformanceReport`, and `A2AConformanceHarness`.

- [ ] **Step 2: Update readiness and docs**

List SDK self-conformance as available. Keep external conformance tests,
OIDC/CA trust governance, DNS/egress controls, and tenant RBAC as remaining
gaps.

- [ ] **Step 3: Verify docs/API tests**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: PASS.

### Task 6: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted tests**

```powershell
uv run pytest tests\channels\test_a2a_conformance.py tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] **Step 2: Run full tests**

```powershell
uv run pytest -q
```

- [ ] **Step 3: Compile and boundary scan**

```powershell
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: pytest and compileall pass; `rg` exits 1 with no matches; diff check
exits 0 aside from existing CRLF warnings.
