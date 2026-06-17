from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agentos import AgentBuilder
from agentos.channels import (
    AsgiAgentApp,
    InMemorySessionLeaseStore,
    InMemorySseEventBuffer,
    InMemorySseTurnControlStore,
    RedisSseEventBuffer,
    RedisSseTurnControlStore,
)
from agentos.compression import CompressionIndex
from agentos.context import ContextRuntime
from agentos.deployment import (
    BackendVerificationRecord,
    DeploymentLiveBackendVerificationProfile,
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    LocalSubprocessWorkerSupervisor,
    PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
    ProductionStatePlaneDeploymentProfile,
)
from agentos.examples.planner_patterns import build_plan_and_execute_example
from agentos.multi import (
    AgentCard,
    PostgresPlanStore,
    PostgresTaskStore,
    RedisAgentMessageQueue,
)
from agentos.persistence import MemoryPersistence, PostgresSessionSnapshotPersistence
from agentos.persistence.base import SessionSnapshot
from agentos.probes import ReferenceLiveBackendProbePack
from agentos.providers import FakeProvider
from agentos.readiness import ProductionReadinessEvidenceBundle
from agentos.registry import (
    NacosAgentCardResolver,
    NacosAgentRegistryAdapter,
    NacosRegistryConfig,
)
from agentos.runtime import (
    DistributedWebRuntimeProfile,
    DistributedWebSessionOperationsProfile,
    SessionState,
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
                "backend_verification": (
                    self.backend_verification.__class__.__name__
                ),
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


def build_production_reference_web_agent(
    *,
    backend_verification_records: Sequence[BackendVerificationRecord] | None = None,
    auth_policy: object | None = None,
    allow_demo_runtime_readiness: bool = False,
    lease_store: object | None = None,
    snapshot_persistence: object | None = None,
    workspace_isolation_profile: object | None = None,
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
    sse_event_buffer = _reference_sse_event_buffer(served_lease_store)
    sse_turn_control = _reference_sse_turn_control(served_lease_store)
    backend_evidence_ready = backend_verification.gate_report().accepted
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
                else (
                    _demo_served_runtime_reason(
                        lease_store=served_lease_store,
                        snapshot_persistence=served_snapshot_persistence,
                    )
                    if backend_evidence_ready and served_demo_runtime
                    else "live backend verification evidence is required"
                )
            ),
        },
    }
    probe_pack = ReferenceLiveBackendProbePack(environment="reference")
    planner_summary = build_plan_and_execute_example()
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
            "summary": planner_summary,
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


def build_reference_app(
    *,
    backend_verification_records: Sequence[BackendVerificationRecord] | None = None,
    auth_policy: object | None = None,
    allow_demo_runtime_readiness: bool = False,
    lease_store: object | None = None,
    snapshot_persistence: object | None = None,
    workspace_isolation_profile: object | None = None,
) -> AsgiAgentApp:
    """Build the ASGI app for the production reference web agent."""

    return build_production_reference_web_agent(
        backend_verification_records=backend_verification_records,
        auth_policy=auth_policy,
        allow_demo_runtime_readiness=allow_demo_runtime_readiness,
        lease_store=lease_store,
        snapshot_persistence=snapshot_persistence,
        workspace_isolation_profile=workspace_isolation_profile,
    ).app


def build_reference_readiness_evidence(
    *,
    backend_verification_records: Sequence[BackendVerificationRecord] | None = None,
    auth_policy: object | None = None,
    allow_demo_runtime_readiness: bool = False,
    lease_store: object | None = None,
    snapshot_persistence: object | None = None,
    workspace_isolation_profile: object | None = None,
) -> dict[str, object]:
    """Return JSON-safe readiness evidence for release gate consumption."""

    return build_production_reference_web_agent(
        backend_verification_records=backend_verification_records,
        auth_policy=auth_policy,
        allow_demo_runtime_readiness=allow_demo_runtime_readiness,
        lease_store=lease_store,
        snapshot_persistence=snapshot_persistence,
        workspace_isolation_profile=workspace_isolation_profile,
    ).as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    """Print JSON evidence for the production reference web agent."""

    parser = argparse.ArgumentParser()
    parser.parse_args(argv)
    print(
        json.dumps(
            build_reference_readiness_evidence(),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return 0


class ReferenceSnapshotAgentFactory:
    """Small deterministic agent factory for durable session examples."""

    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ):
        message_runtime = None
        if snapshot is not None:
            message_runtime = snapshot.message_runtime
        builder = AgentBuilder().provider(FakeProvider([f"ok:{session_id}"]))
        if message_runtime is not None:
            builder = builder.message_runtime(message_runtime)
        return builder.build()

    def create_snapshot(self, *, session_id: str, agent) -> SessionSnapshot:
        return SessionSnapshot(
            session_state=SessionState(id=session_id),
            context_state=ContextRuntime().state,
            message_runtime=agent.query_loop.message_runtime,
            compression_index=CompressionIndex(),
        )


class ReferenceNacosClient:
    """No-network Nacos client used only for adapter identity evidence."""

    agentos_reference_no_network = True

    def register_instance(self, **kwargs: object) -> None:
        return None

    def deregister_instance(self, **kwargs: object) -> None:
        return None

    def list_instances(self, **kwargs: object) -> Sequence[object]:
        return ()


class ReferenceRedisClient:
    """No-network Redis client used only for adapter identity evidence."""

    agentos_reference_no_network = True

    def xgroup_create(self, *args: object, **kwargs: object) -> None:
        return None

    def xadd(self, *args: object, **kwargs: object) -> str:
        return "0-1"

    def xreadgroup(self, *args: object, **kwargs: object) -> list[object]:
        return []

    def xack(self, *args: object, **kwargs: object) -> int:
        return 1


class ReferencePostgresConnection:
    """No-database Postgres connection used only for adapter identity evidence."""

    agentos_reference_no_network = True

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> object:
        raise RuntimeError("reference example does not execute SQL")

    def commit(self) -> None:
        return None


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
) -> ProductionReadinessEvidenceBundle:
    runtime_metadata = runtime_profile.readiness_metadata()
    return ProductionReadinessEvidenceBundle.from_sources(
        {
            "reference_state_plane_stack": {
                "status": "ok" if backend_verification.gate_report().accepted else "failed",
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


def _reference_sse_event_buffer(lease_store: object) -> object:
    redis_url = getattr(lease_store, "backend_url", None)
    redis_client = getattr(lease_store, "_client", None)
    if isinstance(redis_url, str) and redis_url:
        return RedisSseEventBuffer(redis_url, client=redis_client)
    return InMemorySseEventBuffer()


def _reference_sse_turn_control(lease_store: object) -> object:
    redis_url = getattr(lease_store, "backend_url", None)
    redis_client = getattr(lease_store, "_client", None)
    if isinstance(redis_url, str) and redis_url:
        return RedisSseTurnControlStore(redis_url, client=redis_client)
    return InMemorySseTurnControlStore()


def _contains_reference_no_network_fixture(value: object) -> bool:
    if bool(getattr(value, "agentos_reference_no_network", False)):
        return True
    if value.__class__.__name__ in {
        "ReferenceNacosClient",
        "ReferenceRedisClient",
        "ReferencePostgresConnection",
    }:
        return True
    for attribute_name in (
        "_client",
        "client",
        "_connection",
        "connection",
        "_pool",
        "pool",
        "_active_connection",
    ):
        nested = getattr(value, attribute_name, None)
        if nested is not None and bool(
            getattr(nested, "agentos_reference_no_network", False),
        ):
            return True
    return False


def _backend_verification_profile(
    *,
    records: Sequence[BackendVerificationRecord] | None = None,
) -> DeploymentLiveBackendVerificationProfile:
    if records is None:
        return DeploymentLiveBackendVerificationProfile(records=())
    return DeploymentLiveBackendVerificationProfile(
        records=tuple(records),
    )


def _reference_agent_card() -> dict[str, object]:
    card = AgentCard(
        agent_id="production-reference-web-agent",
        name="Production Reference Web Agent",
        description="AgentOS production reference web agent.",
        capabilities=("web", "readiness", "planner"),
        version="0.1.0",
        endpoint="https://agentos.example.invalid",
    )
    return {
        "agent_id": card.agent_id,
        "name": card.name,
        "capabilities": card.capabilities,
        "version": card.version,
        "endpoint": card.endpoint,
    }


def _backend_kind(name: str) -> str:
    return {
        "agent_registry": "nacos",
        "message_queue": "redis",
        "task_store": "postgres",
        "plan_store": "postgres",
        "worker_process_supervisor": "worker_process_supervisor",
        "session_snapshot_persistence": "postgres",
    }[name]


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _json_safe_value(value) for key, value in values.items()}


def _json_safe_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return repr(value)


if __name__ == "__main__":
    raise SystemExit(main())
