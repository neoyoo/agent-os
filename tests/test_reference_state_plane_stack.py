from __future__ import annotations

import json

from agentos.deployment_constants import (
    LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS,
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
)
from agentos.deployment_profiles import (
    DeploymentLiveBackendVerificationProfile,
    ProductionStatePlaneDeploymentProfile,
)
from agentos.deployment_types import BackendVerificationRecord


class NacosAgentRegistryAdapter:
    pass


class NacosAgentCardResolver:
    pass


class RedisAgentMessageQueue:
    pass


class PostgresTaskStore:
    pass


class PostgresPlanStore:
    pass


class WorkerProcessSupervisor:
    pass


class PostgresSessionSnapshotPersistence:
    pass


class DistributedWebRuntimeProfile:
    def readiness_metadata(self) -> dict[str, object]:
        return {
            "profile": self.__class__.__name__,
            "ready": True,
            "session_provider": "DurableAgentSessionProvider",
        }


class AgentServiceReference:
    def readiness_check(self) -> dict[str, object]:
        return {
            "profile": self.__class__.__name__,
            "ok": True,
            "readiness": "reference service",
        }


def passed_backend_verification() -> DeploymentLiveBackendVerificationProfile:
    return DeploymentLiveBackendVerificationProfile(
        records=tuple(
            BackendVerificationRecord(
                backend_name=name,
                backend_kind=LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS[name],
                status="passed",
                checked_at=1781590000.0,
                evidence_ref=f"ci://backend-probes/{name}",
                target_ref=f"deployment://agentos/{name}",
            )
            for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
        ),
    )


def configured_state_plane_profile() -> ProductionStatePlaneDeploymentProfile:
    return ProductionStatePlaneDeploymentProfile(
        configured_components=PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS,
    )


def full_stack_kwargs() -> dict[str, object]:
    return {
        "agent_registry": NacosAgentRegistryAdapter(),
        "agent_card_resolver": NacosAgentCardResolver(),
        "message_queue": RedisAgentMessageQueue(),
        "task_store": PostgresTaskStore(),
        "plan_store": PostgresPlanStore(),
        "worker_process_supervisor": WorkerProcessSupervisor(),
        "session_snapshot_persistence": PostgresSessionSnapshotPersistence(),
        "runtime_profile": DistributedWebRuntimeProfile(),
        "service_reference": AgentServiceReference(),
        "state_plane_profile": configured_state_plane_profile(),
        "live_backend_verification": passed_backend_verification(),
        "metadata": {
            "service": "customer-support",
            "credential": "raw-secret",
            "nested": {"api_token": "raw-token", "zone": "az-a"},
        },
    }


def test_reference_state_plane_stack_builds_json_safe_readiness_bundle() -> None:
    from agentos.state_plane import (
        REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS,
        ReferenceStatePlaneStack,
    )

    stack = ReferenceStatePlaneStack(**full_stack_kwargs())

    bundle = stack.build_readiness_bundle()
    metadata = stack.readiness_metadata()
    encoded = json.dumps(metadata)

    assert stack.missing_components() == ()
    assert set(stack.configured_components()) >= set(
        REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS,
    )
    assert bundle.accepted is True
    assert metadata["ready"] is True
    assert metadata["profile"] == "ReferenceStatePlaneStack"
    assert metadata["component_identities"]["agent_registry"] == (
        "NacosAgentRegistryAdapter"
    )
    assert metadata["component_identities"]["agent_card_resolver"] == (
        "NacosAgentCardResolver"
    )
    assert metadata["component_identities"]["message_queue"] == (
        "RedisAgentMessageQueue"
    )
    assert metadata["component_identities"]["task_store"] == "PostgresTaskStore"
    assert metadata["component_identities"]["plan_store"] == "PostgresPlanStore"
    assert metadata["component_identities"]["worker_process_supervisor"] == (
        "WorkerProcessSupervisor"
    )
    assert metadata["component_identities"]["session_snapshot_persistence"] == (
        "PostgresSessionSnapshotPersistence"
    )
    assert metadata["component_identities"]["runtime_profile"] == (
        "DistributedWebRuntimeProfile"
    )
    assert metadata["component_identities"]["service_reference"] == (
        "AgentServiceReference"
    )
    assert "ProductionStatePlaneDeploymentProfile" in encoded
    assert "DeploymentLiveBackendVerificationProfile" in encoded
    assert "ProductionReadinessEvidenceBundle" in encoded
    assert "reference state plane" in encoded
    assert "readiness source aggregation" in encoded
    assert "component identity evidence" in encoded
    assert "does not create backend clients" in encoded
    assert "credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned" in encoded
    assert "raw-secret" not in encoded
    assert "raw-token" not in encoded


def test_reference_state_plane_stack_blocks_when_required_component_missing() -> None:
    from agentos.state_plane import ReferenceStatePlaneStack

    kwargs = full_stack_kwargs()
    kwargs.pop("message_queue")
    kwargs.pop("live_backend_verification")

    stack = ReferenceStatePlaneStack(**kwargs)
    check = stack.readiness_check()
    bundle = stack.build_readiness_bundle()

    assert "message_queue" in stack.missing_components()
    assert "live_backend_verification" in stack.missing_components()
    assert check["ok"] is False
    assert check["status"] == "failed"
    assert check["block_production_readiness"] is True
    assert bundle.accepted is False
    assert "reference_state_plane_stack" in bundle.blocking_checks
