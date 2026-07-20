from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agentos._redaction import redact_secret_patterns
from agentos.channels import (
    AsgiAgentApp,
    InMemorySessionLeaseStore,
)
from agentos.deployment import (
    BackendVerificationRecord,
    DeploymentLiveBackendVerificationProfile,
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    LocalSubprocessWorkerSupervisor,
    PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
    ProductionStatePlaneDeploymentProfile,
)
from agentos.examples.planner_patterns import build_plan_and_execute_example
from agentos.examples._production_reference_fixtures import (
    ReferenceNacosClient as ReferenceNacosClient,
    ReferencePostgresConnection as ReferencePostgresConnection,
    ReferenceRedisClient as ReferenceRedisClient,
    ReferenceSnapshotAgentFactory as ReferenceSnapshotAgentFactory,
)
from agentos.examples._production_reference_support import (
    backend_verification_profile as _backend_verification_profile,
    contains_reference_no_network_fixture as _contains_reference_no_network_fixture,
    json_safe_mapping as _json_safe_mapping,
    reference_agent_card as _reference_agent_card,
    reference_sse_event_buffer as _reference_sse_event_buffer,
    reference_sse_turn_control as _reference_sse_turn_control,
)
from agentos.multi import (
    PostgresTaskStore,
    RedisAgentMessageQueue,
)
from agentos.multi.postgres_plan import PostgresPlanStore
from agentos.persistence import MemoryPersistence, PostgresSessionSnapshotPersistence
from agentos.probes import ReferenceLiveBackendProbePack
from agentos.readiness import ProductionReadinessEvidenceBundle
from agentos.registry import (
    NacosAgentCardResolver,
    NacosAgentRegistryAdapter,
    NacosRegistryConfig,
)
from agentos.runtime import (
    DistributedWebRuntimeProfile,
    DistributedWebSessionOperationsProfile,
)
from agentos.service import (
    AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS,
    AgentServiceReference,
    AgentServiceReferenceProfile,
)
from agentos.state_plane import ReferenceStatePlaneStack
from agentos.workspace import (
    LocalWorkspaceExecutionBackend,
    WorkspaceExecutionIsolationProfile,
)


REFERENCE_CHECKED_AT = 1781596800.0


@dataclass(slots=True)
class ProductionReferenceWebAgentExample:
    """Deterministic SDK reference composition for a production web agent."""

    service_reference: AgentServiceReference
    runtime_profile: DistributedWebRuntimeProfile
    state_plane_stack: ReferenceStatePlaneStack
    probe_pack: ReferenceLiveBackendProbePack
    backend_verification: DeploymentLiveBackendVerificationProfile
    readiness_bundle: ProductionReadinessEvidenceBundle
    planner_primitive: Mapping[str, object]
    app: AsgiAgentApp
    metadata: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """Return JSON-safe evidence for the reference web agent example."""

        stack_metadata = self.state_plane_stack.readiness_metadata()
        component_identities = dict(stack_metadata["component_identities"])
        component_identities.update(
            {
                "service_reference": self.service_reference.__class__.__name__,
                "runtime_profile": self.runtime_profile.__class__.__name__,
                "served_lease_store": self.runtime_profile.lease_store.__class__.__name__,
                "served_snapshot_persistence": (
                    self.runtime_profile.snapshot_persistence.__class__.__name__
                ),
                "state_plane_stack": self.state_plane_stack.__class__.__name__,
                "probe_pack": self.probe_pack.__class__.__name__,
                "backend_verification": (self.backend_verification.__class__.__name__),
                "readiness_bundle": self.readiness_bundle.__class__.__name__,
                "app": self.app.__class__.__name__,
            },
        )
        return _json_safe_mapping(
            {
                "example": "production_reference_web_agent",
                "phase": "Phase 101: Production Reference Example",
                "agent_form": "distributed web agent",
                "state_plane": "Nacos/Redis/Postgres state plane",
                "mode": self.metadata.get("mode"),
                "readiness_endpoint": "/ready",
                "backend_verification": (
                    self.backend_verification.readiness_metadata()
                ),
                "readiness_bundle": self.readiness_bundle.as_dict(),
                "state_plane_stack": stack_metadata,
                "probe_pack": self.probe_pack.as_dict(),
                "planner_primitive": dict(self.planner_primitive),
                "component_identities": component_identities,
                "metadata": dict(self.metadata),
                "sdk_owned": (
                    "production reference web agent",
                    "AgentServiceReference",
                    "DistributedWebRuntimeProfile",
                    "ReferenceStatePlaneStack",
                    "ReferenceLiveBackendProbePack",
                    "ProductionReadinessEvidenceBundle",
                    "readiness endpoint",
                    "planner primitive",
                    "does not create backend clients",
                ),
                "deployment_owned": (
                    "deployment-owned real infrastructure",
                    "Nacos/Redis/Postgres deployment and credentials",
                    "backend verification execution",
                    "migration execution",
                    "Kubernetes/systemd/autoscaling",
                    "gateway/TLS/CORS/WAF",
                    "tenant directory",
                    "CI/CD, rollout, rollback, alerting, and runbooks",
                    "physical sandbox isolation",
                ),
            },
        )


async def build_production_reference_web_agent(
    *,
    backend_verification_records: Sequence[BackendVerificationRecord] | None = None,
    auth_policy: object | None = None,
    allow_demo_runtime_readiness: bool = False,
    lease_store: object | None = None,
    snapshot_persistence: object | None = None,
    workspace_isolation_profile: object | None = None,
    state_plane_backend_targets: Mapping[str, str] | None = None,
) -> ProductionReferenceWebAgentExample:
    """Build the deterministic production reference web agent composition."""

    backend_verification = _backend_verification_profile(
        records=backend_verification_records,
    )
    served_lease_store = lease_store or InMemorySessionLeaseStore()
    served_snapshot_persistence = snapshot_persistence or MemoryPersistence()
    served_demo_runtime = _is_demo_served_runtime(
        lease_store=served_lease_store,
        snapshot_persistence=served_snapshot_persistence,
    )
    backend_evidence_ready = backend_verification.gate_report().accepted
    stream_resume_evidence_ready = backend_evidence_ready and not served_demo_runtime
    sse_event_buffer = _reference_sse_event_buffer(
        served_lease_store,
        stream_resume_evidence_ready=stream_resume_evidence_ready,
    )
    sse_turn_control = _reference_sse_turn_control(
        served_lease_store,
        stream_resume_evidence_ready=stream_resume_evidence_ready,
    )
    if backend_verification_records is None and served_demo_runtime:
        mode = "demo_without_live_backend_evidence"
    elif backend_verification_records is None:
        mode = "production_runtime_without_live_backend_evidence"
    elif not served_demo_runtime:
        mode = "production_runtime_with_imported_live_backend_evidence"
    elif allow_demo_runtime_readiness:
        mode = "demo_runtime_with_imported_live_backend_evidence"
    else:
        mode = "demo_runtime_blocks_production_readiness"
    runtime_profile = DistributedWebRuntimeProfile(
        agent_factory=ReferenceSnapshotAgentFactory(),
        lease_store=served_lease_store,
        snapshot_persistence=served_snapshot_persistence,
        owner_id="production-reference-node",
        auth_policy=auth_policy,
        sse_event_buffer=sse_event_buffer,
        sse_turn_control=sse_turn_control,
        sse_turn_control_owner_id="production-reference-node",
        sse_turn_control_ttl_seconds=30.0,
        sse_turn_control_poll_interval_seconds=1.0,
        session_lease_heartbeat_interval_seconds=10.0,
    )
    distributed_session_profile = DistributedWebSessionOperationsProfile(
        configured_components=(
            "durable_session_provider",
            "lease_store",
            "snapshot_persistence",
            "snapshot_migration",
            "lease_ttl_policy",
            "stale_lease_recovery",
            "shared_sse_event_buffer",
            "sse_turn_control",
            "session_lease_heartbeat",
            "credential_policy",
            "auth_tenant_policy",
            "workspace_policy",
            "live_backend_verification",
        ),
    )
    state_plane_profile = ProductionStatePlaneDeploymentProfile(
        configured_components=PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
    )
    local_workspace_isolation_profile = WorkspaceExecutionIsolationProfile(
        configured_components=(
            "workspace_policy",
            "tool_path_sandbox",
            "capability_allowlist",
            "execution_backend",
            "process_isolation",
            "resource_limits",
            "network_policy",
            "audit_logging",
        ),
    )
    effective_workspace_isolation_profile = (
        workspace_isolation_profile
        or _reference_workspace_isolation_check(local_workspace_isolation_profile)
    )
    service_reference = AgentServiceReference(
        runtime_profile=runtime_profile,
        service_profile=AgentServiceReferenceProfile(
            configured_components=AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS,
        ),
        distributed_session_profile=distributed_session_profile,
        state_plane_profile=state_plane_profile,
        workspace_isolation_profile=effective_workspace_isolation_profile,
        workspace_execution_backend=LocalWorkspaceExecutionBackend(),
    )
    readiness_bundle = _reference_readiness_bundle(
        runtime_profile=runtime_profile,
        service_reference=service_reference,
        state_plane_profile=state_plane_profile,
        distributed_session_profile=distributed_session_profile,
        backend_verification=backend_verification,
        allow_demo_runtime_readiness=allow_demo_runtime_readiness,
        state_plane_backend_targets=state_plane_backend_targets,
    )
    state_plane_stack = _reference_state_plane_stack(
        runtime_profile=runtime_profile,
        service_reference=service_reference,
        state_plane_profile=state_plane_profile,
        distributed_session_profile=distributed_session_profile,
        backend_verification=backend_verification,
        readiness_bundle=readiness_bundle,
    )
    service_reference.readiness_checks = {
        "reference_state_plane_stack": state_plane_stack.readiness_check,
        backend_verification.probe_name: backend_verification.readiness_check,
        "production_reference_web_agent": lambda: {
            "status": "ok" if readiness_bundle.accepted else "failed",
            "ok": readiness_bundle.accepted,
            "ready": readiness_bundle.accepted,
            "profile": "ProductionReferenceWebAgentExample",
            "mode": mode,
            "blocking_reason": (
                ""
                if readiness_bundle.accepted
                else ", ".join(readiness_bundle.blocking_checks)
            ),
        },
    }
    probe_pack = ReferenceLiveBackendProbePack(environment="reference")
    planner_context_projection = await build_plan_and_execute_example()
    example = ProductionReferenceWebAgentExample(
        service_reference=service_reference,
        runtime_profile=runtime_profile,
        state_plane_stack=state_plane_stack,
        probe_pack=probe_pack,
        backend_verification=backend_verification,
        readiness_bundle=readiness_bundle,
        planner_primitive={
            "pattern": "plan-and-execute",
            "runtime": "PlannerRuntime",
            "context_projection": planner_context_projection,
        },
        app=service_reference.build_asgi_app(),
        metadata={
            "mode": mode,
            "release_scope": "first production SDK release",
            "boundary": "SDK reference example, not platform provisioning",
            "does not create backend clients": True,
            "deployment-owned real infrastructure": True,
            "demo_runtime": served_demo_runtime,
        },
    )
    return example


async def build_reference_app(
    *,
    backend_verification_records: Sequence[BackendVerificationRecord] | None = None,
    auth_policy: object | None = None,
    allow_demo_runtime_readiness: bool = False,
    lease_store: object | None = None,
    snapshot_persistence: object | None = None,
    workspace_isolation_profile: object | None = None,
    state_plane_backend_targets: Mapping[str, str] | None = None,
) -> AsgiAgentApp:
    """Build the ASGI app for the production reference web agent."""

    example = await build_production_reference_web_agent(
        backend_verification_records=backend_verification_records,
            auth_policy=auth_policy,
            allow_demo_runtime_readiness=allow_demo_runtime_readiness,
            lease_store=lease_store,
            snapshot_persistence=snapshot_persistence,
        workspace_isolation_profile=workspace_isolation_profile,
        state_plane_backend_targets=state_plane_backend_targets,
    )
    return example.app


async def build_reference_readiness_evidence(
    *,
    backend_verification_records: Sequence[BackendVerificationRecord] | None = None,
    auth_policy: object | None = None,
    allow_demo_runtime_readiness: bool = False,
    lease_store: object | None = None,
    snapshot_persistence: object | None = None,
    workspace_isolation_profile: object | None = None,
    state_plane_backend_targets: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return JSON-safe readiness evidence for release gate consumption."""

    example = await build_production_reference_web_agent(
        backend_verification_records=backend_verification_records,
            auth_policy=auth_policy,
            allow_demo_runtime_readiness=allow_demo_runtime_readiness,
            lease_store=lease_store,
            snapshot_persistence=snapshot_persistence,
        workspace_isolation_profile=workspace_isolation_profile,
        state_plane_backend_targets=state_plane_backend_targets,
    )
    return example.as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    """Print JSON evidence for the production reference web agent."""

    parser = argparse.ArgumentParser()
    parser.parse_args(argv)
    print(
        json.dumps(
            asyncio.run(build_reference_readiness_evidence()),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return 0


def _reference_state_plane_stack(
    *,
    runtime_profile: DistributedWebRuntimeProfile,
    service_reference: AgentServiceReference,
    state_plane_profile: ProductionStatePlaneDeploymentProfile,
    distributed_session_profile: DistributedWebSessionOperationsProfile,
    backend_verification: DeploymentLiveBackendVerificationProfile,
    readiness_bundle: ProductionReadinessEvidenceBundle,
) -> ReferenceStatePlaneStack:
    nacos_client = ReferenceNacosClient()
    postgres_connection = ReferencePostgresConnection()
    return ReferenceStatePlaneStack(
        agent_registry=NacosAgentRegistryAdapter(
            client=nacos_client,
            config=NacosRegistryConfig(service_name="agentos.reference"),
        ),
        agent_card_resolver=NacosAgentCardResolver(
            client=nacos_client,
            config=NacosRegistryConfig(service_name="agentos.reference"),
        ),
        message_queue=RedisAgentMessageQueue(
            "redis://reference.invalid/0",
            client=ReferenceRedisClient(),
        ),
        task_store=PostgresTaskStore(
            "postgresql://reference.invalid/agentos",
            connection=postgres_connection,
        ),
        plan_store=PostgresPlanStore(
            "postgresql://reference.invalid/agentos",
            connection=postgres_connection,
        ),
        worker_process_supervisor=LocalSubprocessWorkerSupervisor(
            clock=lambda: REFERENCE_CHECKED_AT,
        ),
        session_snapshot_persistence=PostgresSessionSnapshotPersistence(
            "postgresql://reference.invalid/agentos",
            connection=postgres_connection,
        ),
        runtime_profile=runtime_profile,
        service_reference=service_reference,
        state_plane_profile=state_plane_profile,
        distributed_session_profile=distributed_session_profile,
        live_backend_verification=backend_verification,
        readiness_bundle=readiness_bundle,
        metadata={
            "agent_card": _reference_agent_card(),
            "does not create backend clients": True,
            "deployment-owned real infrastructure": True,
        },
    )


def _reference_readiness_bundle(
    *,
    runtime_profile: DistributedWebRuntimeProfile,
    service_reference: AgentServiceReference,
    state_plane_profile: ProductionStatePlaneDeploymentProfile,
    distributed_session_profile: DistributedWebSessionOperationsProfile,
    backend_verification: DeploymentLiveBackendVerificationProfile,
    allow_demo_runtime_readiness: bool,
    state_plane_backend_targets: Mapping[str, str] | None,
) -> ProductionReadinessEvidenceBundle:
    runtime_metadata = runtime_profile.readiness_metadata()
    return ProductionReadinessEvidenceBundle.from_sources(
        {
            "reference_state_plane_stack": {
                "status": "ok"
                if backend_verification.gate_report().accepted
                else "failed",
                "ok": backend_verification.gate_report().accepted,
                "profile": "ReferenceStatePlaneStack",
                "component identity evidence": True,
                "does not create backend clients": True,
                "blocking_reason": (
                    ""
                    if backend_verification.gate_report().accepted
                    else "live backend verification evidence is required"
                ),
            },
            "reference_served_runtime": _reference_served_runtime_check(
                runtime_profile=runtime_profile,
                allow_demo_runtime_readiness=allow_demo_runtime_readiness,
            ),
            "reference_served_backend_binding": (
                _reference_served_backend_binding_check(
                    runtime_profile=runtime_profile,
                    backend_verification=backend_verification,
                    state_plane_backend_targets=state_plane_backend_targets,
                )
            ),
            "workspace_execution_isolation": service_reference.workspace_isolation_profile,
            "production_state_plane": state_plane_profile,
            "distributed_web_runtime_profile": {
                **runtime_metadata,
                "status": "ok",
                "ok": True,
                "ready": True,
                "profile": runtime_profile.__class__.__name__,
            },
            "agent_service_reference": service_reference,
            "distributed_session_profile": distributed_session_profile,
            "live_backend_verification": backend_verification,
        },
        required_checks=(
            "reference_state_plane_stack",
            "reference_served_runtime",
            "reference_served_backend_binding",
            "workspace_execution_isolation",
            "production_state_plane",
            "distributed_web_runtime_profile",
            "agent_service_reference",
            "distributed_session_profile",
            "live_backend_verification",
        ),
        bundle_name="production_reference_web_agent",
        metadata={
            "Phase 101: Production Reference Example": True,
            "production reference web agent": True,
            "readiness source aggregation": True,
            "does not create backend clients": True,
            "deployment-owned real infrastructure": True,
        },
    )


def _reference_served_runtime_check(
    *,
    runtime_profile: DistributedWebRuntimeProfile,
    allow_demo_runtime_readiness: bool,
) -> dict[str, object]:
    lease_store_name = runtime_profile.lease_store.__class__.__name__
    snapshot_persistence_name = runtime_profile.snapshot_persistence.__class__.__name__
    blocking_reason = _demo_served_runtime_reason(
        lease_store=runtime_profile.lease_store,
        snapshot_persistence=runtime_profile.snapshot_persistence,
    )
    demo_runtime = bool(blocking_reason)
    ok = not demo_runtime
    return {
        "status": "ok" if ok else "failed",
        "ok": ok,
        "ready": ok,
        "profile": "ProductionReferenceServedRuntime",
        "lease_store": lease_store_name,
        "snapshot_persistence": snapshot_persistence_name,
        "demo_runtime": demo_runtime,
        "allow_demo_runtime_readiness": allow_demo_runtime_readiness,
        "block_production_readiness": not ok,
        "blocking_reason": "" if ok else blocking_reason,
        "sdk_owned": (
            "production reference runtime binding evidence",
            "demo runtime guard",
        ),
        "deployment_owned": (
            "inject RedisSessionLeaseStore and PostgresSessionSnapshotPersistence",
            "serve the ASGI app with real state-plane backends",
        ),
    }


def _reference_served_backend_binding_check(
    *,
    runtime_profile: DistributedWebRuntimeProfile,
    backend_verification: DeploymentLiveBackendVerificationProfile,
    state_plane_backend_targets: Mapping[str, str] | None,
) -> dict[str, object]:
    served_targets = _served_backend_targets(
        runtime_profile,
        state_plane_backend_targets=state_plane_backend_targets,
    )
    required_target_backends = (
        () if not served_targets else LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    )
    if not served_targets:
        return {
            "status": "ok",
            "ok": True,
            "ready": True,
            "profile": "ProductionReferenceServedBackendBinding",
            "required_target_backends": (),
            "served_targets": {},
            "evidence_targets": {},
            "mismatched_backends": (),
            "missing_target_backends": (),
            "block_production_readiness": False,
            "blocking_reason": "",
        }
    records_by_name = {
        record.backend_name: record for record in backend_verification.records
    }
    evidence_targets = {
        name: redact_secret_patterns(record.target_ref)
        for name, record in records_by_name.items()
        if name in required_target_backends and record.target_ref is not None
    }
    missing = tuple(
        name
        for name in required_target_backends
        if name not in served_targets or not evidence_targets.get(name)
    )
    mismatched = tuple(
        name
        for name, expected in served_targets.items()
        if name in evidence_targets and evidence_targets[name] != expected
    )
    ok = not missing and not mismatched
    return {
        "status": "ok" if ok else "failed",
        "ok": ok,
        "ready": ok,
        "profile": "ProductionReferenceServedBackendBinding",
        "required_target_backends": required_target_backends,
        "served_targets": served_targets,
        "evidence_targets": evidence_targets,
        "mismatched_backends": mismatched,
        "missing_target_backends": missing,
        "block_production_readiness": not ok,
        "blocking_reason": (
            ""
            if ok
            else "live backend verification target_ref does not match served runtime"
        ),
        "sdk_owned": (
            "production reference runtime backend target binding",
            "BackendVerificationRecord.target_ref comparison",
        ),
        "deployment_owned": (
            "emit backend verification records for the exact served Redis/Postgres targets",
            "rotate credentials without changing the stable non-secret target identity",
        ),
    }


def _served_backend_targets(
    runtime_profile: DistributedWebRuntimeProfile,
    *,
    state_plane_backend_targets: Mapping[str, str] | None = None,
) -> dict[str, str]:
    if _is_demo_served_runtime(
        lease_store=runtime_profile.lease_store,
        snapshot_persistence=runtime_profile.snapshot_persistence,
    ):
        return {}
    targets = _explicit_state_plane_backend_targets(state_plane_backend_targets)
    redis_url = getattr(runtime_profile.lease_store, "backend_url", None)
    if isinstance(redis_url, str) and redis_url:
        targets["message_queue"] = redact_secret_patterns(redis_url)
    postgres_dsn = getattr(
        runtime_profile.snapshot_persistence,
        "backend_dsn",
        None,
    )
    if isinstance(postgres_dsn, str) and postgres_dsn:
        targets["session_snapshot_persistence"] = redact_secret_patterns(postgres_dsn)
    return targets


def _explicit_state_plane_backend_targets(
    state_plane_backend_targets: Mapping[str, str] | None,
) -> dict[str, str]:
    if state_plane_backend_targets is None:
        return {}
    return {
        name: redact_secret_patterns(target)
        for name, target in state_plane_backend_targets.items()
        if name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
        and isinstance(target, str)
        and bool(target.strip())
    }


class _ReferenceWorkspaceIsolationCheck:
    probe_name = "workspace_execution_isolation"

    def __init__(self, profile: WorkspaceExecutionIsolationProfile) -> None:
        self._profile = profile

    def readiness_metadata(self) -> dict[str, object]:
        metadata = dict(self._profile.readiness_metadata())
        return {
            **metadata,
            "ready": False,
            "local_workspace_execution_backend": "LocalWorkspaceExecutionBackend",
            "block_production_readiness": True,
            "blocking_reason": (
                "deployment-owned workspace isolation evidence is required; "
                "LocalWorkspaceExecutionBackend is a reference backend, not a "
                "production isolation boundary"
            ),
            "deployment_owned": (
                *tuple(metadata.get("deployment_owned", ())),
                "provide workspace isolation readiness evidence",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        return {
            **self.readiness_metadata(),
            "status": "failed",
            "ok": False,
        }


def _reference_workspace_isolation_check(
    profile: WorkspaceExecutionIsolationProfile,
) -> _ReferenceWorkspaceIsolationCheck:
    return _ReferenceWorkspaceIsolationCheck(profile)


def _is_demo_served_runtime(
    *,
    lease_store: object,
    snapshot_persistence: object,
) -> bool:
    return bool(
        _demo_served_runtime_reason(
            lease_store=lease_store,
            snapshot_persistence=snapshot_persistence,
        ),
    )


def _demo_served_runtime_reason(
    *,
    lease_store: object,
    snapshot_persistence: object,
) -> str:
    lease_store_name = lease_store.__class__.__name__
    snapshot_persistence_name = snapshot_persistence.__class__.__name__
    if (
        lease_store_name == "InMemorySessionLeaseStore"
        or snapshot_persistence_name == "MemoryPersistence"
    ):
        return "reference app uses demo Memory/InMemory runtime bindings"
    if _contains_reference_no_network_fixture(
        lease_store,
    ) or _contains_reference_no_network_fixture(snapshot_persistence):
        return "reference app uses no-network reference runtime backend clients"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
