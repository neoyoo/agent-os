from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[2]


def assert_phrase(text: str, expected: str) -> None:
    normalized = " ".join(text.split())
    assert expected in text or expected in normalized


def test_quickstart_and_architecture_docs_exist() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.strip()
    assert "Quickstart" in readme
    assert (ROOT / "docs" / "quickstart.md").exists()
    assert (ROOT / "docs" / "architecture.md").exists()


def test_production_hardening_todo_is_checked_off() -> None:
    todo = (ROOT / "docs" / "todo-production-hardening.md").read_text(
        encoding="utf-8",
    )

    assert "- [ ]" not in todo


def test_required_examples_exist() -> None:
    for name in [
        "streaming_agent.py",
        "multi_agent_dispatch.py",
        "mcp_agent.py",
        "persistent_agent.py",
        "planner_patterns.py",
        "production_reference_web_agent.py",
    ]:
        text = (ROOT / "src" / "agentos" / "examples" / name).read_text(
            encoding="utf-8",
        )
        assert 'if __name__ == "__main__"' in text


def test_phase_100_release_hardening_gate_docs_exist_and_name_release_evidence() -> None:
    release_hardening = (ROOT / "docs" / "release-hardening.md").read_text(
        encoding="utf-8",
    )
    api_stability = (ROOT / "docs" / "api-stability.md").read_text(
        encoding="utf-8",
    )
    migration_index = (ROOT / "docs" / "migrations" / "README.md").read_text(
        encoding="utf-8",
    )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for expected in [
        "Phase 100: Release Hardening",
        "release hardening gate",
        "release candidate evidence",
        "public API audit",
        "stable API",
        "experimental API",
        "machine-readable public API inventory",
        "API stability classification",
        "migration index",
        "README / quickstart / examples alignment",
        "CHANGELOG.md",
        "full test suite evidence",
        "diff/commit hygiene",
        "SDK-owned release evidence",
        "release evidence validator",
        "validate_release_evidence_manifest",
        "validate_release_candidate_evidence_manifest",
        "ReleaseEvidenceValidationReport",
        "RELEASE_EVIDENCE_REQUIRED_GATES",
        "scripts/generate_release_evidence.py",
        "requires expected branch, commit, and version identity",
        "pending or failed independent review blocks release-candidate promotion",
        "secret-like value redaction",
        "does not run CI/CD, signing, publishing, deployment approval",
        "docs/release-hardening.md",
        "docs/api-stability.md",
        "docs/public-api-inventory.json",
        "docs/migrations/README.md",
        "tests/architecture/test_public_api.py",
        "uv run pytest -q",
        "git diff --check",
        "QueryLoop",
        "single async QueryLoop",
        "agentos.sync",
    ]:
        assert_phrase(release_hardening, expected)

    for expected in [
        "API stability classification",
        "stable API",
        "experimental API",
        "public API audit",
        "machine-readable public API inventory",
        "deprecation",
        "migration notes",
        "boundary-first",
        "tests/architecture/test_public_api.py",
        "docs/public-api-inventory.json",
        "ReleaseEvidenceValidationReport",
        "validate_release_evidence_manifest",
        "RELEASE_EVIDENCE_REQUIRED_GATES",
    ]:
        assert_phrase(api_stability, expected)

    for expected in [
        "CompareAndSavePlanStore",
        "PlanStoreRecord",
        "PlanConflictError",
        "optimistic concurrency",
        "plan-store concurrency",
    ]:
        assert_phrase(api_stability, expected)

    for expected in [
        "migration index",
        "migrate:up",
        "migrate:down",
        "deployment-owned execution",
        "Postgres",
        "SQLite",
        "Qdrant",
    ]:
        assert_phrase(migration_index, expected)

    for expected in [
        "Phase 100: Release Hardening",
        "Release Scope Re-baseline",
        "Reference State Plane Stack",
        "Live Backend Probe Pack",
        "SDK Skill / Spec Generator Finalization",
        "Release Hardening",
        "Phase 101: Production Reference Example",
        "Production Reference Example",
        "production reference web agent",
    ]:
        assert_phrase(changelog, expected)


def test_release_evidence_manifest_example_is_machine_readable_and_non_certifying() -> None:
    manifest_path = ROOT / "docs" / "release-evidence.example.json"
    release_hardening = (ROOT / "docs" / "release-hardening.md").read_text(
        encoding="utf-8",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema"] == "agentos.release_evidence"
    assert manifest["certification_claim"] == "non-certifying-sdk-evidence"
    assert manifest["release_candidate"]["branch"] == (
        "review/agentos-sdk-architecture-20260611"
    )
    assert manifest["release_candidate"]["generated_at"] == "<iso8601>"
    assert manifest["sdk_owned"] is True
    assert manifest["deployment_owned"] == [
        "CI/CD execution",
        "artifact signing",
        "publishing",
        "deployment approval",
        "rollout and rollback",
    ]
    gates = manifest["gates"]
    for name in [
        "public_api_audit",
        "full_test_suite",
        "compileall",
        "diff_hygiene",
        "runtime_boundary_scan",
        "docs_alignment",
        "migration_index",
        "api_stability_inventory",
        "production_reference_honesty",
        "planner_plan_store_concurrency",
        "workspace_security_policy",
        "independent_review",
    ]:
        assert name in gates
        assert set(gates[name]) >= {
            "status",
            "command",
            "evidence_ref",
            "required",
            "last_verified_at",
        }
    assert "docs/release-evidence.example.json" in release_hardening
    assert "machine-readable release evidence manifest" in release_hardening
    assert "non-certifying SDK evidence" in release_hardening


def test_release_evidence_docs_separate_ignored_candidate_from_ordinary_tests() -> None:
    release_hardening = (ROOT / "docs" / "release-hardening.md").read_text(
        encoding="utf-8",
    )

    for expected in [
        "Ordinary unit tests do not read the ignored local",
        "docs/release-evidence.json",
        "enable the explicit gate first",
        "AGENTOS_VALIDATE_LOCAL_RELEASE_EVIDENCE",
        "Release automation must use the non-skippable validator CLI",
        "scripts/validate_release_evidence.py",
        "--manifest docs/release-evidence.json",
        "--branch <current-branch>",
        "--commit <current-commit>",
        "--version <pyproject-version>",
    ]:
        assert_phrase(release_hardening, expected)


def test_release_evidence_docs_reference_split_test_commands() -> None:
    release_hardening = (ROOT / "docs" / "release-hardening.md").read_text(
        encoding="utf-8",
    )
    phase0_plan = (
        ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-11-agentos-phase0-baseline-remediation-implementation-plan.md"
    ).read_text(encoding="utf-8")
    normal_matrix = (
        "tests/test_release_evidence.py "
        "tests/test_release_evidence_local.py "
        "tests/test_release_evidence_cli.py"
    )
    local_command = "-m pytest tests/test_release_evidence_local.py -q"
    pending_cli_node = (
        "tests/test_release_evidence_cli.py::"
        "test_release_evidence_validator_cli_rejects_pending_"
        "independent_review_with_matching_identity"
    )

    assert normal_matrix in release_hardening
    assert local_command in release_hardening
    assert normal_matrix in phase0_plan
    assert local_command in phase0_plan
    assert pending_cli_node in phase0_plan
    assert (
        "tests/test_release_evidence.py::"
        "test_release_evidence_validator_cli_rejects_pending_"
        "independent_review_with_matching_identity"
    ) not in phase0_plan


def test_public_api_inventory_manifest_is_machine_readable_release_evidence() -> None:
    inventory_path = ROOT / "docs" / "public-api-inventory.json"
    release_hardening = (ROOT / "docs" / "release-hardening.md").read_text(
        encoding="utf-8",
    )
    api_stability = (ROOT / "docs" / "api-stability.md").read_text(
        encoding="utf-8",
    )

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    assert inventory["schema"] == "agentos.public_api_inventory"
    assert inventory["schema_version"] == 1
    assert {"branch", "commit"}.isdisjoint(inventory)
    assert inventory["signature_format"] == (
        "normalized inspect.signature string or non-callable"
    )
    assert "agentos" in inventory["modules"]
    assert "agentos.channels" in inventory["modules"]
    assert "agentos.multi" in inventory["modules"]
    assert "docs/public-api-inventory.json" in release_hardening
    assert "docs/public-api-inventory.json" in api_stability


def test_readme_and_quickstart_are_aligned_with_release_scope() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")

    for text in (readme, quickstart):
        for expected in [
            "first production SDK release",
            "terminal agent",
            "single-node web agent",
            "distributed web agent",
            "team/planner/A2A primitive",
            "production state plane",
            "readiness evidence",
            "Sandbox / Docker / E2B / microVM / enterprise runner adapter",
            "non-blocking future adapter",
            "trusted tools only",
            "deployment-owned isolation",
            "future adapter",
            "docs/release-hardening.md",
            "docs/api-stability.md",
            "docs/migrations/README.md",
            "src/agentos/examples/production_reference_web_agent.py",
        ]:
            assert_phrase(text, expected)


def test_quickstart_web_example_names_explicit_local_auth_policy() -> None:
    quickstart = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    skill_quick_start = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "quick-start.md"
    ).read_text(encoding="utf-8")

    for text in (quickstart, skill_quick_start):
        assert "AllowAllChannelAuthPolicy" in text
        assert "auth_policy=AllowAllChannelAuthPolicy()" in text
        assert "local/dev" in text
        assert "RejectAllChannelAuthPolicy" in text
        assert "production-facing defaults" in text


def test_phase_100_guidance_is_linked_from_readiness_audit_roadmap_and_skill() -> None:
    production_readiness = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    audit = (ROOT / "docs" / "agentos-objective-coverage-audit.md").read_text(
        encoding="utf-8",
    )
    roadmap = (
        ROOT
        / "docs"
        / "plans"
        / "2026-06-11-agentos-sdk-architecture-review-roadmap.md"
    ).read_text(encoding="utf-8")
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for text in (production_readiness, audit, roadmap, skill, spec_generation):
        for expected in [
            "Phase 100: Release Hardening",
            "release hardening gate",
            "release candidate evidence",
            "public API audit",
            "stable API",
            "experimental API",
            "migration index",
            "README / quickstart / examples alignment",
            "CHANGELOG.md",
            "full test suite evidence",
            "diff/commit hygiene",
            "SDK-owned release evidence",
            "does not run CI/CD, signing, publishing, deployment approval",
            "docs/release-hardening.md",
            "docs/api-stability.md",
            "docs/migrations/README.md",
        ]:
            assert_phrase(text, expected)


def test_phase_101_production_reference_example_is_linked_from_release_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    production_readiness = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    audit = (ROOT / "docs" / "agentos-objective-coverage-audit.md").read_text(
        encoding="utf-8",
    )
    roadmap = (
        ROOT
        / "docs"
        / "plans"
        / "2026-06-11-agentos-sdk-architecture-review-roadmap.md"
    ).read_text(encoding="utf-8")
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")
    quick_start = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "quick-start.md"
    ).read_text(encoding="utf-8")

    for text in (
        readme,
        quickstart,
        production_readiness,
        audit,
        roadmap,
        skill,
        spec_generation,
        quick_start,
    ):
        for expected in [
            "Phase 101: Production Reference Example",
            "production reference web agent",
            "AgentServiceReference",
            "DistributedWebRuntimeProfile",
            "Nacos/Redis/Postgres state plane",
            "readiness endpoint",
            "backend verification",
            "ProductionReadinessEvidenceBundle",
            "ReferenceStatePlaneStack",
            "ReferenceLiveBackendProbePack",
            "planner primitive",
            "does not create backend clients",
            "deployment-owned real infrastructure",
            "src/agentos/examples/production_reference_web_agent.py",
        ]:
            assert_phrase(text, expected)
