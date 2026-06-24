from pathlib import Path
import re

from agentos.readiness import (
    REQUIRED_READINESS_DIMENSIONS,
    list_agent_form_readiness,
)


ROOT = Path(__file__).resolve().parents[2]


def assert_phrase(text: str, expected: str) -> None:
    normalized = " ".join(text.split())
    assert expected in text or expected in normalized


def test_production_readiness_doc_covers_matrix_forms_and_dimensions() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )

    assert "agentos.readiness" in text
    assert "get_agent_form_readiness" in text
    assert "production_readiness" in text
    for form in list_agent_form_readiness():
        assert form.form_id in text
        assert form.name in text
    for dimension in REQUIRED_READINESS_DIMENSIONS:
        assert dimension in text


def test_agent_os_skill_uses_readiness_lookup_for_production_specs() -> None:
    requirements = (
        ROOT / ".claude" / "skills" / "agent-os" / "flow" / "01-requirements.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    for text in (requirements, spec_generation):
        assert "get_agent_form_readiness" in text
        assert "required_app_glue" in text
    assert "docs/production-readiness.md" in agent_forms


def test_agent_os_spec_schema_uses_structured_production_readiness_dimensions() -> None:
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for dimension in REQUIRED_READINESS_DIMENSIONS:
        pattern = re.compile(
            rf"    {re.escape(dimension)}:\n"
            r"      level: direct \| primitives-ready \| future-extension \| not-applicable\n"
            r"      evidence: \[<SDK evidence, tests, docs, or deployment evidence ref>\]\n"
            r"      gap: <remaining SDK or deployment-owned gap, empty when none>",
        )
        assert pattern.search(spec_generation), dimension

    legacy_scalar_pattern = re.compile(
        r"    (session_state|concurrency|auth|rate_limit|timeout|retry|observability|workspace|protocol|persistence|schema_migration): direct \| primitives-ready",
    )
    assert legacy_scalar_pattern.search(spec_generation) is None


def test_production_readiness_doc_describes_skill_release_governance_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    quick_start = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "quick-start.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "SkillReleaseManifest",
        "SkillReleaseDriftReport",
        "SkillReleaseFile",
        "build_skill_release_manifest",
        "compare_skill_release_manifests",
        "repository skill",
        "installed user-level skill",
        "release manifest",
        "drift report",
        "version synchronization",
        "install/copy/publish/sign approval remains deployment-owned",
    ]:
        assert expected in text
        assert expected in skill

    for expected in [
        "build_skill_release_manifest",
        "compare_skill_release_manifests",
        "SkillReleaseDriftReport",
        "repository skill",
        "installed user-level skill",
    ]:
        assert expected in quick_start


def test_production_readiness_doc_describes_production_state_plane_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    persistence = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "persistence.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "ProductionStatePlaneDeploymentProfile",
        "production_state_plane",
        "agent_registry",
        "message_queue",
        "task_store",
        "plan_store",
        "worker_process_supervisor",
        "session_snapshot_persistence",
        "state_plane_boundary_policy",
        "NacosAgentRegistryAdapter",
        "NacosAgentCardResolver",
        "NacosRegistryClient",
        "NacosRegistryEvidence",
        "discovery-only",
        "not task truth",
        "not plan truth",
        "not session snapshot storage",
        "not message queue",
        "not worker runtime state",
        "RedisAgentMessageQueue",
        "PostgresTaskStore",
        "PostgresPlanStore",
        "WorkerProcessSupervisor",
        "SessionSnapshotPersistence",
        "registry is not task truth",
        "queue is not final task or plan state",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in architecture
        assert expected in persistence
        assert expected in spec_generation


def test_production_readiness_doc_describes_live_backend_verification_evidence_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    persistence = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "persistence.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "BackendVerificationRecord",
        "DeploymentLiveBackendVerificationGateReport",
        "DeploymentLiveBackendVerificationProfile",
        "LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS",
        "deployment_live_backend_verification",
        "block_production_readiness",
        "agent_registry",
        "message_queue",
        "task_store",
        "plan_store",
        "worker_process_supervisor",
        "session_snapshot_persistence",
        "missing or failed backend evidence",
        "backend check execution",
        "credentials and secret distribution",
        "CI matrix execution",
        "alert routing and runbooks",
    ]:
        assert expected in text
        assert expected in skill
        assert expected in agent_forms
        assert expected in architecture
        assert expected in persistence
        assert expected in spec_generation


def test_production_readiness_doc_describes_direct_task_claim_target_fence() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    multi_agent = (
        ROOT / ".claude" / "skills" / "agent-os" / "modules" / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "Direct Task Claim Safety",
        "claim_task",
        "claim_queued",
        "target_agent_id",
        "ExpertAgentRunner",
        "dedicated worker",
        "shared worker pool",
        "compatibility/shared-pool behavior",
    ]:
        assert_phrase(text, expected)
        assert_phrase(multi_agent, expected)


def test_production_readiness_doc_describes_live_backend_verification_runner_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    persistence = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "persistence.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "BackendVerificationInvocationPlan",
        "BackendVerificationRunner",
        "BackendVerificationCliRunner",
        "BackendVerificationReportImporter",
        "BackendVerificationReportImportError",
        "DeploymentLiveBackendVerificationRunResult",
        "argv-only",
        "no shell parsing",
        "report path",
        "stdout JSON",
        "bounded stdout/stderr summaries",
        "env_keys",
        "secret values",
        "no backend client claim",
        "reference runner",
        "not a live backend client",
    ]:
        assert expected in text
        assert expected in skill
        assert expected in agent_forms
        assert expected in architecture
        assert expected in persistence
        assert expected in spec_generation


def test_production_readiness_doc_describes_readiness_evidence_bundle_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "ProductionReadinessEvidenceBundle",
        "ReadinessEvidenceCheck",
        "ReadinessEvidenceStatus",
        "blocking_checks",
        "missing_required_checks",
        "block_production_readiness",
        "release gate evidence bundle",
        "consumes existing readiness/profile/backend evidence",
        "does not execute real infrastructure checks",
        "sdk_owned",
        "deployment_owned",
        "JSON-safe evidence bundle",
    ]:
        assert expected in text
        assert expected in skill
        assert expected in agent_forms
        assert expected in architecture
        assert expected in spec_generation


def test_production_readiness_doc_describes_release_scope_rebaseline_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    release_scope = (ROOT / "docs" / "release-scope.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    requirements = (
        ROOT / ".claude" / "skills" / "agent-os" / "flow" / "01-requirements.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "Phase 96: Release Scope Re-baseline",
        "release scope re-baseline",
        "first production SDK release",
        "trusted tools",
        "internal service orchestration",
        "terminal agent",
        "single-node web agent",
        "distributed web agent",
        "team/planner/A2A primitive",
        "production state plane",
        "readiness evidence",
        "Sandbox / Docker / E2B / microVM / enterprise runner adapter",
        "non-blocking future adapter",
        "not a release blocker",
        "does not promise physical isolation for untrusted code execution",
        "WorkspaceExecutionBackend",
        "SandboxBackend",
        "LocalWorkspaceExecutionBackend",
        "policy/capability/path pre-check",
        "audit evidence",
        "sandbox posture",
        "trusted tools only",
        "deployment-owned isolation",
        "future adapter",
        "docs/release-scope.md",
    ]:
        assert_phrase(text, expected)
        assert_phrase(release_scope, expected)
        assert_phrase(skill, expected)
        assert_phrase(agent_forms, expected)
        assert_phrase(architecture, expected)
        assert_phrase(multi_agent, expected)
        assert_phrase(spec_generation, expected)

    for expected in [
        "sandbox posture",
        "trusted tools only",
        "deployment-owned isolation",
        "future adapter",
    ]:
        assert_phrase(requirements, expected)


def test_production_readiness_doc_describes_reference_state_plane_stack() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")
    persistence = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "persistence.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "Phase 97: Reference State Plane Stack",
        "ReferenceStatePlaneStack",
        "ReferenceStatePlaneStackProfile",
        "REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS",
        "reference state plane",
        "readiness source aggregation",
        "component identity evidence",
        "NacosAgentRegistryAdapter",
        "RedisAgentMessageQueue",
        "PostgresTaskStore",
        "PostgresPlanStore",
        "WorkerProcessSupervisor",
        "LocalSubprocessWorkerSupervisor",
        "SessionSnapshotPersistence",
        "PostgresSessionSnapshotPersistence",
        "AgentServiceReference",
        "DistributedWebRuntimeProfile",
        "ProductionReadinessEvidenceBundle",
        "does not create backend clients",
        (
            "credentials, migrations, CI matrix execution, alert routing "
            "and runbooks remain deployment-owned"
        ),
    ]:
        assert_phrase(text, expected)
        assert_phrase(skill, expected)
        assert_phrase(agent_forms, expected)
        assert_phrase(architecture, expected)
        assert_phrase(multi_agent, expected)
        assert_phrase(persistence, expected)
        assert_phrase(spec_generation, expected)


def test_production_readiness_doc_describes_reference_live_backend_probe_pack() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
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

    for expected in [
        "Phase 98: Live Backend Probe Pack",
        "ReferenceLiveBackendProbePack",
        "ReferenceLiveBackendProbeSpec",
        "REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME",
        "agentos.examples.live_backend_probe",
        "Nacos probe",
        "Redis probe",
        "Postgres task/plan/session probe",
        "worker supervisor probe",
        "readiness bundle aggregation",
        "BackendVerificationInvocationPlan",
        "DeploymentLiveBackendVerificationRunResult",
        "ProductionReadinessEvidenceBundle",
        "does not create backend clients",
        "default status is unknown",
        "non-certifying example",
        (
            "credentials, migrations, CI matrix execution, alert routing "
            "and runbooks remain deployment-owned"
        ),
    ]:
        assert_phrase(text, expected)
        assert_phrase(skill, expected)
        assert_phrase(spec_generation, expected)


def test_production_readiness_doc_describes_sdk_spec_generator_finalization() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    requirements = (
        ROOT / ".claude" / "skills" / "agent-os" / "flow" / "01-requirements.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")
    implementation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "03-implementation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "Phase 99: SDK Skill / Spec Generator Finalization",
        "spec generator finalization",
        "production agent design constraint generator",
        "production_design_constraints",
        "must explicitly choose",
        "agent form",
        "runtime profile",
        "state plane components",
        "persistence backend",
        "registry backend",
        "queue backend",
        "worker supervisor",
        "A2A exposure",
        "planner/team mode",
        "production readiness checklist",
        "sandbox posture: trusted tools only | deployment-owned isolation | future adapter",
        "does not create deployment-owned infrastructure",
        "SDK-owned constraint template",
    ]:
        assert_phrase(text, expected)
        assert_phrase(skill, expected)
        assert_phrase(requirements, expected)
        assert_phrase(spec_generation, expected)
        assert_phrase(implementation, expected)


def test_production_readiness_doc_describes_nacos_registry_adapter_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    persistence = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "persistence.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "NacosAgentRegistryAdapter",
        "NacosAgentCardResolver",
        "NacosRegistryClient",
        "NacosRegistryConfig",
        "NacosRegistryEvidence",
        "Nacos namespace_id evidence",
        "Nacos namespace_id is passed to register, unregister, list, resolve, and discover",
        "AgentCard-to-Nacos metadata projection",
        "healthy Nacos instances",
        "capability-based discovery",
        "JSON-safe evidence",
        "discovery-only",
        "not task truth",
        "not plan truth",
        "not session snapshot storage",
        "not message queue",
        "not worker runtime state",
        "credentials",
        "live backend verification",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in architecture
        assert expected in persistence
        assert expected in multi_agent
        assert expected in spec_generation


def test_production_readiness_doc_describes_a2a_default_policy_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "RejectAllA2AInboundAuthPolicy",
        "default inbound A2A operation auth is fail-closed",
        "A2AOperationServer default rejects unauthenticated peers",
        "AllowAllA2AInboundAuthPolicy is explicit local/dev opt-in",
        "A2AOperationClient default public HTTPS egress policy",
        "local/dev peers must opt in explicitly",
    ]:
        assert_phrase(text, expected)
        assert_phrase(agent_forms, expected)
        assert_phrase(multi_agent, expected)


def test_production_readiness_doc_describes_a2a_self_conformance_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    assert "A2AConformanceHarness" in text
    assert "A2AExternalConformanceReportImporter" in text
    assert "A2AExternalConformanceExecutionProfile" in text
    assert "A2AExternalConformanceExecutionRecord" in text
    assert "A2AExternalConformanceGateReport" in text
    assert "A2AExternalConformanceRunner" in text
    assert "A2AExternalConformanceCliRunner" in text
    assert "A2AExternalConformanceInvocationPlan" in text
    assert "A2AExternalConformanceInvocationGateReport" in text
    assert "external conformance execution record" in text
    assert "external conformance gate report" in text
    assert "external conformance CLI runner" in text
    assert "argv-only" in text
    assert "no shell parsing" in text
    assert "report path" in text
    assert "stdout JSON" in text
    assert "bounded stdout/stderr summaries" in text
    assert "env_keys" in text
    assert "secret values" in text
    assert "external conformance invocation plan" in text
    assert "external conformance invocation gate report" in text
    assert "SDK self-conformance" in text
    assert "message/stream request" in text
    assert "message stream event" in text
    assert "tasks/resubscribe request" in text
    assert "task resubscribe statusUpdate event" in text
    assert "external conformance result import" in text
    assert "external_suite_runner" in text
    assert "target_endpoint" in text
    assert "credential_policy" in text
    assert "network_egress_policy" in text
    assert "version_matrix" in text
    assert "ci_artifact_retention" in text
    assert "failure_alerting" in text
    assert "agent-card" in text
    assert "message-send" in text
    assert "message-stream" in text
    assert "push-notification-config" in text
    assert "no certification claim" in text
    assert "external suite invocation planning" in text
    assert "External suite execution" in text
    assert "A2AConformanceHarness" in agent_forms
    assert "message/stream request" in agent_forms
    assert "message stream event" in agent_forms
    assert "tasks/resubscribe request" in agent_forms
    assert "task resubscribe statusUpdate event" in agent_forms
    assert "A2AExternalConformanceReportImporter" in agent_forms
    assert "A2AExternalConformanceExecutionProfile" in agent_forms
    assert "A2AExternalConformanceExecutionRecord" in agent_forms
    assert "A2AExternalConformanceGateReport" in agent_forms
    assert "A2AExternalConformanceCliRunner" in agent_forms
    assert "argv-only" in agent_forms
    assert "no shell parsing" in agent_forms
    assert "A2AExternalConformanceInvocationPlan" in agent_forms
    assert "A2AExternalConformanceInvocationGateReport" in agent_forms
    assert "SDK-owned external conformance suite execution" not in agent_forms
    assert "extension negotiation" not in (
        agent_forms.split("Do not market it as full A2A compliance until", 1)[-1]
    )
    assert "A2AExternalConformanceExecutionProfile" in multi_agent
    assert "A2AExternalConformanceExecutionRecord" in multi_agent
    assert "A2AExternalConformanceGateReport" in multi_agent
    assert "A2AExternalConformanceCliRunner" in multi_agent
    assert "external conformance CLI runner" in multi_agent
    assert "A2AExternalConformanceInvocationPlan" in multi_agent
    assert "A2AExternalConformanceInvocationGateReport" in multi_agent
    assert "external_suite_runner" in multi_agent
    assert "push-notification-config" in multi_agent
    assert "official external suite selection/installation" in multi_agent
    assert "CI matrix execution" in multi_agent
    assert "certification attestation governance" in multi_agent


def test_production_readiness_doc_describes_a2a_oidc_claims_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")

    assert "OidcClaimsA2AInboundAuthPolicy" in text
    assert "HmacA2AJwtVerifier" in text
    assert "JwksA2AJwtVerifier" in text
    assert "JWT/OIDC claims validation" in text
    assert "OidcDiscoveryMetadataProvider" in text
    assert "OIDC discovery metadata" in text
    assert "RS256/JWKS JWT verification" in text
    assert "RS256/JWKS JWT verification" not in text.split(
        "RS256/JWKS JWT verification",
        1,
    )[-1].split("Health automation", 1)[0]
    assert "OIDC discovery" not in text.split(
        "This JWT/OIDC claims validation",
        1,
    )[-1].split("Use", 1)[0]
    assert "OidcClaimsA2AInboundAuthPolicy" in agent_forms
    assert "OidcDiscoveryMetadataProvider" in agent_forms
    assert "JwksA2AJwtVerifier" in agent_forms


def test_production_readiness_doc_describes_a2a_tenant_rbac_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "A2ATenantRbacRule" in text
    assert "ClaimsTenantRbacA2AInboundAuthPolicy" in text
    assert "claims-backed authorization policy boundary" in normalized
    assert "tenant directory and role assignment lifecycle policy" in text
    assert "tenant RBAC mapping policy" not in text
    assert "A2ATenantRbacRule" in agent_forms
    assert "ClaimsTenantRbacA2AInboundAuthPolicy" in agent_forms
    assert "claims-backed tenant RBAC policy" in agent_forms
    assert "tenant RBAC mapping" not in agent_forms


def test_production_readiness_doc_describes_a2a_bearer_rotation_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "A2ABearerCredential",
        "RotatingBearerA2ACredentialStore",
        "RotatingBearerA2AAuthProvider",
        "RotatingBearerA2AInboundAuthPolicy",
    ]:
        assert expected in text
        assert expected in agent_forms
    assert "current outbound bearer credential" in text
    assert "active overlapping inbound bearer credentials" in text
    assert "credential issuance and secret distribution policy" in text
    assert "KMS/secret-manager governance" in text
    assert "credential rotation" not in multi_agent.split(
        "Remaining A2A gaps are",
        1,
    )[-1].split(".", 1)[0]
    assert "credential issuance and secret distribution" in multi_agent


def test_production_readiness_doc_describes_a2a_per_peer_rate_limit_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "A2AOperationRateLimitPolicy",
        "PeerKeyA2AOperationRateLimitPolicy",
        "A2APeerIdResolver",
        "A2ARateLimitError",
    ]:
        assert expected in text
        assert expected in agent_forms
    normalized = text.lower()
    assert "rate limit exceeded" in normalized
    assert "distributed/global quota" in normalized
    assert "gateway enforcement" in normalized
    assert "billing tiers" in normalized
    assert "per-peer rate limit" in multi_agent
    assert "per-peer A2A rate policy is app-owned" not in text


def test_production_readiness_doc_describes_a2a_task_resubscribe_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    normalized = " ".join(text.split())
    assert "tasks/resubscribe" in text
    assert "handle_task_resubscribe" in text
    assert "A2AOperationClient.task_resubscribe" in text
    assert "one-shot cursor resubscribe" in normalized
    assert "task subscribe operation boundary" in normalized
    assert (
        "Automatic reconnect loops, durable cursor storage, fan-out, "
        "backpressure, gateway quota, billing, and credential issuance "
        "remain deployment-owned"
    ) in normalized
    assert "tasks/resubscribe" in agent_forms
    assert "A2AOperationClient.task_resubscribe" in agent_forms
    assert "one-shot cursor resubscribe" in agent_forms
    assert "task subscribe operation boundary" in agent_forms
    assert "tasks/resubscribe" in multi_agent
    assert "A2AOperationClient.task_resubscribe" in multi_agent


def test_production_readiness_doc_describes_a2a_message_stream_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    normalized = " ".join(text.split())
    assert "message/stream" in text
    assert "/a2a/message:stream" in text
    assert "A2AOperationClient.stream_message" in text
    assert "A2AOperationClient.stream_message_events" in text
    assert "A2AMessageStreamEvent" in text
    assert "parse_a2a_sse_events" in text
    assert "message stream operation boundary" in normalized
    assert "client-side operation initiation boundary" in normalized
    assert "typed client-side SSE event consumption boundary" in normalized
    assert "SSE client consumption" not in normalized
    assert "reconnects, backpressure, durable stream cursors, and fan-out" in normalized
    assert "message/stream" in agent_forms
    assert "A2AOperationClient.stream_message" in agent_forms
    assert "A2AOperationClient.stream_message_events" in agent_forms
    assert "A2AMessageStreamEvent" in agent_forms
    assert "parse_a2a_sse_events" in agent_forms
    assert "message stream operation boundary" in agent_forms
    assert "/a2a/message:stream" in multi_agent
    assert "A2AOperationClient.stream_message" in multi_agent
    assert "A2AOperationClient.stream_message_events" in multi_agent
    assert "A2AMessageStreamEvent" in multi_agent


def test_production_readiness_doc_describes_a2a_stream_lifecycle_profile() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "A2AStreamLifecycleDeploymentProfile",
        "automatic reconnect loops",
        "durable cursor storage",
        "fan-out",
        "backpressure",
        "gateway quota",
        "billing",
        "credential issuance",
        "process supervision",
        "DNS pinning",
        "enterprise egress proxy",
        "CA rollout",
        "tenant directory lifecycle",
        "external conformance execution",
    ]:
        assert expected in text
        assert expected in agent_forms
    assert "A2AStreamLifecycleDeploymentProfile" in multi_agent


def test_production_readiness_doc_describes_a2a_push_worker_health_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")

    assert "A2APushNotificationHealthPolicy" in text
    assert "A2APushNotificationHealthReport" in text
    assert "A2APushNotificationDeploymentProfile" in text
    assert "classify daemon state" in text
    assert "readiness_check()" in text
    assert "A2APushNotificationHealthPolicy" in agent_forms
    assert "A2APushNotificationHealthReport" in agent_forms
    assert "A2APushNotificationDeploymentProfile" in agent_forms
    assert "worker monitoring and alerting policy" not in text


def test_production_readiness_doc_describes_planner_orchestration_profile() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "PlannerOrchestrationDeploymentProfile",
        "PlannerDecompositionPolicyDeploymentProfile",
        "PlanDecompositionGatePolicy",
        "PlanDecompositionGateReport",
        "PlannerRuntime.gate_decomposition_proposal",
        "plan_gate_decomposition_proposal",
        "PlanDecompositionValidationReport",
        "PlannerRuntime.validate_decomposition",
        "PlannerLlmDecompositionGovernanceProfile",
        "PlannerLlmGovernanceEvidenceRecord",
        "PlannerLlmGovernanceEvidenceGateReport",
        "PlannerRuntime.gate_llm_governance_evidence",
        "planner LLM governance execution evidence",
        "component_refs",
        "evidence_refs",
        "budget_policy",
        "governance reference readiness payloads",
        "per-proposal governance evidence gate",
        "deployment-owned prompt/model/approval/evaluation/validation execution",
        "prompt text and prompt review workflow",
        "PlanSchedulerTickReport",
        "PlanClaimedSchedulerTickReport",
        "PlanClaimedSchedulerTickSkip",
        "PlannerClaimedSchedulerDaemon",
        "PlannerClaimedSchedulerDaemonState",
        "PlannerClaimedSchedulerDaemonError",
        "claimed scheduler daemon polling",
        "PlannerSchedulerGovernanceDeploymentProfile",
        "planner scheduler governance profile",
        "plan_discovery_policy",
        "tenant_routing_policy",
        "global_fairness_policy",
        "leader_election_policy",
        "PlannerWorkerDispatchSupervisionProfile",
        "PlanClaimSweepReport",
        "PlanClaimSweepSkip",
        "PlanClaimSweepStore",
        "PlannerRuntime.sweep_expired_claims",
        "PlannerStaleClaimSweepProfile",
        "PlannerSchedulablePlan",
        "PlannerRuntime.schedulable_plans",
        "plan_schedulable_plans",
        "schedulable plan selection",
        "PlanClaimStore",
        "InMemoryPlanClaimStore",
        "PostgresPlanClaimStore",
        "PlanClaimRecord",
        "PlannerRuntime.claim_schedulable_plans",
        "plan_claim_schedulable_plans",
        "PlannerRuntime.claimed_scheduler_tick",
        "plan_claimed_scheduler_tick",
        "claim-before-tick scheduler boundary",
        "planner worker dispatch supervision profile",
        "stale claim sweep boundary",
        "planner stale claim sweep profile",
        "stale_claim_sweep_schedule",
        "sweep_safety_window",
        "claimed_scheduler_tick_loop",
        "metrics_alerting",
        "plan claim/lease boundary",
        "2026-06-16-postgres-plan-claims.sql",
        "PlannerSchedulerDaemon",
        "PlannerSchedulerDaemonState",
        "plan_scheduler_tick",
        "explicitly supplied plan ids",
        "prompt_policy",
        "output_schema",
        "validation_gate",
        "template_mapping_policy",
        "model_routing_policy",
        "evaluation_policy",
        "one-shot scheduler tick",
        "decomposition_policy",
        "dag_scheduler",
        "worker_dispatch_loop",
        "compensation_policy",
        "plan_store",
        "worker_supervision",
        "plan discovery",
        "tenant routing",
        "global fairness",
        "stale lease recovery policy",
        "distributed scheduler locks",
        "process supervision",
        "scheduler governance readiness metadata",
        "automatic LLM decomposition policy",
        "LLM prompt/model/approval/evaluation policy",
        "worker dispatch loop",
        "dispatch supervision payload",
        "compensation orchestration",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in multi_agent


def test_production_readiness_doc_describes_worker_process_lifecycle_profile() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "WorkerProcessLifecycleDeploymentProfile",
        "worker_process_lifecycle",
        "process_supervisor",
        "restart_policy",
        "graceful_shutdown",
        "readiness_probe",
        "scaling_policy",
        "migration_policy",
        "live_backend_verification",
        "process supervisor or job runner",
        "horizontal scaling policy",
        "alert routing and runbooks",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in multi_agent


def test_production_readiness_doc_describes_worker_reference_supervisor() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "WorkerProcessSpec",
        "WorkerProcessState",
        "WorkerProcessSupervisor",
        "LocalSubprocessWorkerSupervisor",
        "team_worker",
        "planner_worker",
        "a2a_push_worker",
        "JSON-safe lifecycle evidence",
        "exit_code",
        "started_at",
        "stopped_at",
        "stop_requested_at",
        "env_keys",
        "does not inherit the host environment by default",
        "WorkerProcessSpec rejects secret-like metadata keys",
        "argv-only",
        "no shell parsing",
        "not a Kubernetes, systemd, autoscaling, or secret-distribution layer",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in architecture
        assert expected in multi_agent
        assert expected in spec_generation


def test_production_readiness_doc_describes_workspace_execution_isolation_profile() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "WorkspaceExecutionIsolationProfile",
        "workspace_policy",
        "tool_path_sandbox",
        "capability_allowlist",
        "execution_backend",
        "process_isolation",
        "resource_limits",
        "network_policy",
        "audit_logging",
        "WorkspaceToolSandboxPolicy",
        "ToolPathSandboxRule",
        "path escape pre-check",
        "tool capability pre-check",
        "OS/container sandboxing",
        "filesystem mount policy",
        "network egress policy",
        "CPU and memory limits",
        "secret redaction",
        "audit logging backend",
        "live sandbox backend verification",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in architecture
    assert "WorkspaceExecutionIsolationProfile" in multi_agent


def test_production_readiness_doc_describes_workspace_backend_boundary() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    multi_agent = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "multi-agent.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "flow"
        / "02-spec-generation.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "WorkspaceExecutionBackend",
        "SandboxBackend",
        "WorkspaceExecutionRequest",
        "WorkspaceExecutionResult",
        "WorkspaceExecutionPolicy",
        "LocalWorkspaceExecutionBackend",
        "argv-only",
        "JSON-safe execution evidence",
        "env_keys",
        "does not inherit the host environment by default",
        "Docker/E2B/enterprise runner",
        "not a production isolation boundary",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in architecture
        assert expected in multi_agent
        assert expected in spec_generation


def test_production_readiness_doc_describes_distributed_web_session_operations_profile() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
    persistence = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "persistence.md"
    ).read_text(encoding="utf-8")

    for expected in [
        "DistributedWebSessionOperationsProfile",
        "durable_session_provider",
        "lease_store",
        "snapshot_persistence",
        "snapshot_migration",
        "lease_ttl_policy",
        "stale_lease_recovery",
        "credential_policy",
        "auth_tenant_policy",
        "workspace_policy",
        "live_backend_verification",
        "DurableAgentSessionProvider",
        "RedisSessionLeaseStore",
        "PostgresSessionSnapshotPersistence",
        "acquire/hydrate/save/release lifecycle",
        "Redis/Postgres credentials",
        "migration execution",
        "lease TTL tuning",
        "stale lease recovery policy",
        "auth and tenant integration",
        "live backend verification",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in architecture
        assert expected in persistence


def test_production_readiness_doc_describes_agent_service_reference_layer() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    agent_forms = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "agent-forms.md"
    ).read_text(encoding="utf-8")
    architecture = (
        ROOT
        / ".claude"
        / "skills"
        / "agent-os"
        / "modules"
        / "architecture.md"
    ).read_text(encoding="utf-8")
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

    for expected in [
        "Agent Service Reference Layer",
        "AgentServiceReference",
        "AgentServiceReferenceProfile",
        "AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS",
        "AsgiAgentApp composition",
        "DistributedWebRuntimeProfile injection",
        "auth/rate-limit hook injection",
        "readiness check aggregation",
        "JSON-safe readiness evidence",
        "reference service",
        "not a platform",
        "gateway/TLS/CORS/WAF",
        "tenant directory",
        "Kubernetes/systemd/autoscaling",
        "live backend verification",
    ]:
        assert expected in text
        assert expected in agent_forms
        assert expected in architecture
        assert expected in spec_generation

    for expected in [
        "AgentServiceReference",
        "AgentServiceReferenceProfile",
        "reference service",
    ]:
        assert expected in quick_start


def test_production_readiness_doc_describes_production_reference_example() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
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
        "demo runtime blocks production readiness by default",
        "src/agentos/examples/production_reference_web_agent.py",
        "tests/examples/test_production_reference_web_agent.py",
    ]:
        assert_phrase(text, expected)
        assert_phrase(skill, expected)
        assert_phrase(spec_generation, expected)
        assert_phrase(quick_start, expected)
