from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentos.channels import AsgiAgentApp
from agentos.channels.auth import ChannelAuthError
from agentos.deployment import (
    BackendVerificationRecord,
    DeploymentLiveBackendVerificationProfile,
    LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
)
from agentos.probes import ReferenceLiveBackendProbePack
from agentos.readiness import ProductionReadinessEvidenceBundle
from agentos.runtime import DistributedWebRuntimeProfile
from agentos.service import AgentServiceReference
from agentos.state_plane import ReferenceStatePlaneStack


async def call_asgi(
    app: object,
    *,
    method: str = "GET",
    path: str = "/",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[dict[str, Any]]:
    messages = [{"type": "http.request", "body": b"", "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, object]:
        if messages:
            return messages.pop(0)
        await asyncio.Future()
        raise AssertionError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers or [],
        },
        receive,
        send,
    )
    return sent


def response_body(sent: list[dict[str, Any]]) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )


def response_status(sent: list[dict[str, Any]]) -> int:
    for message in sent:
        if message["type"] == "http.response.start":
            return int(message["status"])
    raise AssertionError("missing response start")


class HeaderTokenAuth:
    def __init__(self, token: str) -> None:
        self._token = token

    def authorize(self, headers: Mapping[str, str]) -> None:
        if headers.get("x-agentos-token") != self._token:
            raise ChannelAuthError("missing token")


def auth_headers() -> list[tuple[bytes, bytes]]:
    return [(b"x-agentos-token", b"reference-token")]


class DeploymentRedisClient:
    def set(self, *args: object, **kwargs: object) -> bool:
        return True

    def get(self, *args: object, **kwargs: object) -> object | None:
        return None

    def eval(self, *args: object, **kwargs: object) -> int:
        return 1


class DeploymentPostgresConnection:
    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> object:
        raise RuntimeError("deployment evidence test does not execute SQL")

    def commit(self) -> None:
        return None


class DeploymentWorkspaceIsolationProfile:
    probe_name = "workspace_execution_isolation"

    def readiness_metadata(self) -> dict[str, object]:
        return {
            "profile": "DeploymentWorkspaceIsolationProfile",
            "ready": True,
            "backend": "deployment-owned",
            "block_production_readiness": False,
        }

    def readiness_check(self) -> dict[str, object]:
        return {
            **self.readiness_metadata(),
            "status": "ok",
            "ok": True,
        }


def test_production_reference_web_agent_module_has_main_entrypoint() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "agentos"
        / "examples"
        / "production_reference_web_agent.py"
    )

    assert 'if __name__ == "__main__"' in path.read_text(encoding="utf-8")


def backend_verification_records(
    target_refs: Mapping[str, str] | None = None,
) -> tuple[BackendVerificationRecord, ...]:
    target_refs = dict(target_refs or {})
    return tuple(
        BackendVerificationRecord(
            backend_name=name,
            backend_kind={
                "agent_registry": "nacos",
                "message_queue": "redis",
                "task_store": "postgres",
                "plan_store": "postgres",
                "worker_process_supervisor": "worker_process_supervisor",
                "session_snapshot_persistence": "postgres",
            }[name],
            status="passed",
            checked_at=1781596800.0,
            evidence_ref=f"ci://agentos/live-backend/{name}",
            target_ref=target_refs.get(
                name,
                {
                    "message_queue": "redis://deployment.example/0",
                    "session_snapshot_persistence": (
                        "postgresql://deployment.example/agentos"
                    ),
                }.get(name, f"deployment://agentos/{name}"),
            ),
            metadata={"source": "imported deployment evidence"},
        )
        for name in LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS
    )


def readiness_check_evidence(
    evidence: dict[str, Any],
    check_name: str,
) -> dict[str, Any]:
    for check in evidence["readiness_bundle"]["checks"]:
        if check["check_name"] == check_name:
            return check["evidence"]
    raise AssertionError(f"missing readiness check: {check_name}")


def test_production_reference_web_agent_builds_reference_composition_without_faking_backend_readiness() -> None:
    from agentos.examples.production_reference_web_agent import (
        ProductionReferenceWebAgentExample,
        build_production_reference_web_agent,
    )

    example = build_production_reference_web_agent()
    evidence = example.as_dict()
    encoded = json.dumps(evidence, sort_keys=True)

    assert isinstance(example, ProductionReferenceWebAgentExample)
    assert isinstance(example.service_reference, AgentServiceReference)
    assert isinstance(example.runtime_profile, DistributedWebRuntimeProfile)
    assert isinstance(example.state_plane_stack, ReferenceStatePlaneStack)
    assert isinstance(example.probe_pack, ReferenceLiveBackendProbePack)
    assert isinstance(
        example.backend_verification,
        DeploymentLiveBackendVerificationProfile,
    )
    assert isinstance(
        example.readiness_bundle,
        ProductionReadinessEvidenceBundle,
    )
    assert isinstance(example.app, AsgiAgentApp)
    assert example.readiness_bundle.accepted is False
    assert evidence["mode"] == "demo_without_live_backend_evidence"
    assert evidence["backend_verification"]["ready"] is False
    assert evidence["backend_verification"]["missing_backends"] == list(
        LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS,
    )
    assert "reference fixture" not in encoded
    assert "ci://agentos/reference-backend" not in encoded
    assert evidence["example"] == "production_reference_web_agent"
    assert evidence["agent_form"] == "distributed web agent"
    assert evidence["state_plane"] == "Nacos/Redis/Postgres state plane"
    assert evidence["readiness_endpoint"] == "/ready"
    assert evidence["planner_primitive"]["pattern"] == "plan-and-execute"
    assert evidence["planner_primitive"]["runtime"] == "PlannerRuntime"
    assert evidence["planner_primitive"]["summary"]["status"] == "running"
    assert evidence["component_identities"]["service_reference"] == (
        "AgentServiceReference"
    )
    assert evidence["component_identities"]["runtime_profile"] == (
        "DistributedWebRuntimeProfile"
    )
    assert evidence["component_identities"]["state_plane_stack"] == (
        "ReferenceStatePlaneStack"
    )
    assert evidence["component_identities"]["probe_pack"] == (
        "ReferenceLiveBackendProbePack"
    )
    assert evidence["component_identities"]["readiness_bundle"] == (
        "ProductionReadinessEvidenceBundle"
    )
    assert evidence["component_identities"]["agent_registry"] == (
        "NacosAgentRegistryAdapter"
    )
    assert evidence["component_identities"]["message_queue"] == (
        "RedisAgentMessageQueue"
    )
    assert evidence["component_identities"]["task_store"] == "PostgresTaskStore"
    assert evidence["component_identities"]["plan_store"] == "PostgresPlanStore"
    assert evidence["component_identities"]["session_snapshot_persistence"] == (
        "PostgresSessionSnapshotPersistence"
    )
    assert evidence["component_identities"]["worker_process_supervisor"] == (
        "LocalSubprocessWorkerSupervisor"
    )
    assert "does not create backend clients" in encoded
    assert "deployment-owned real infrastructure" in encoded
    assert "raw-secret" not in encoded
    assert "raw-token" not in encoded


def test_production_reference_web_agent_separates_imported_backend_evidence_from_served_demo_runtime() -> None:
    from agentos.examples.production_reference_web_agent import (
        build_production_reference_web_agent,
    )

    example = build_production_reference_web_agent(
        backend_verification_records=backend_verification_records(),
    )
    evidence = example.as_dict()

    assert example.readiness_bundle.accepted is False
    assert evidence["mode"] == "demo_runtime_blocks_production_readiness"
    assert evidence["backend_verification"]["ready"] is True
    assert evidence["backend_verification"]["missing_backends"] == []
    assert evidence["readiness_bundle"]["blocking_checks"] == [
        "reference_served_runtime",
        "workspace_execution_isolation",
        "agent_service_reference",
    ]
    assert readiness_check_evidence(
        evidence,
        "workspace_execution_isolation",
    )["block_production_readiness"] is True


def test_production_reference_web_agent_marks_demo_runtime_explicitly_without_production_readiness() -> None:
    from agentos.examples.production_reference_web_agent import (
        build_production_reference_web_agent,
    )

    example = build_production_reference_web_agent(
        backend_verification_records=backend_verification_records(),
        allow_demo_runtime_readiness=True,
    )
    evidence = example.as_dict()

    assert example.readiness_bundle.accepted is False
    assert evidence["mode"] == "demo_runtime_with_imported_live_backend_evidence"
    assert evidence["backend_verification"]["ready"] is True
    assert evidence["readiness_bundle"]["blocking_checks"] == [
        "reference_served_runtime",
        "workspace_execution_isolation",
        "agent_service_reference",
    ]
    assert readiness_check_evidence(evidence, "reference_served_runtime")[
        "allow_demo_runtime_readiness"
    ] is True


def test_production_reference_web_agent_blocks_reference_fixture_clients_wrapped_in_production_adapters() -> None:
    from agentos.channels import RedisSessionLeaseStore
    from agentos.examples.production_reference_web_agent import (
        ReferencePostgresConnection,
        ReferenceRedisClient,
        build_production_reference_web_agent,
    )
    from agentos.persistence import PostgresSessionSnapshotPersistence

    example = build_production_reference_web_agent(
        backend_verification_records=backend_verification_records(),
        lease_store=RedisSessionLeaseStore(
            "redis://reference.invalid/0",
            client=ReferenceRedisClient(),
        ),
        snapshot_persistence=PostgresSessionSnapshotPersistence(
            "postgresql://reference.invalid/agentos",
            connection=ReferencePostgresConnection(),
        ),
    )
    evidence = example.as_dict()

    assert example.readiness_bundle.accepted is False
    assert evidence["mode"] == "demo_runtime_blocks_production_readiness"
    assert evidence["readiness_bundle"]["blocking_checks"] == [
        "reference_served_runtime",
        "workspace_execution_isolation",
        "agent_service_reference",
    ]
    assert readiness_check_evidence(
        evidence,
        "reference_served_runtime",
    )["demo_runtime"] is True
    assert readiness_check_evidence(evidence, "reference_served_runtime")[
        "blocking_reason"
    ] == "reference app uses no-network reference runtime backend clients"
    assert evidence["component_identities"]["served_lease_store"] == "RedisSessionLeaseStore"
    assert evidence["component_identities"]["served_snapshot_persistence"] == (
        "PostgresSessionSnapshotPersistence"
    )


def test_production_reference_web_agent_requires_deployment_owned_runtime_backends_for_production_readiness() -> None:
    from agentos.channels import RedisSessionLeaseStore
    from agentos.examples.production_reference_web_agent import (
        build_production_reference_web_agent,
    )
    from agentos.persistence import PostgresSessionSnapshotPersistence

    example = build_production_reference_web_agent(
        backend_verification_records=backend_verification_records(),
        lease_store=RedisSessionLeaseStore(
            "redis://deployment.example/0",
            client=DeploymentRedisClient(),
        ),
        snapshot_persistence=PostgresSessionSnapshotPersistence(
            "postgresql://deployment.example/agentos",
            connection=DeploymentPostgresConnection(),
        ),
        workspace_isolation_profile=DeploymentWorkspaceIsolationProfile(),
    )
    evidence = example.as_dict()

    assert example.readiness_bundle.accepted is True
    assert evidence["mode"] == "production_runtime_with_imported_live_backend_evidence"
    assert evidence["readiness_bundle"]["blocking_checks"] == []
    assert readiness_check_evidence(
        evidence,
        "reference_served_runtime",
    )["demo_runtime"] is False


def test_production_reference_web_agent_blocks_backend_evidence_for_different_served_runtime_targets() -> None:
    from agentos.channels import RedisSessionLeaseStore
    from agentos.examples.production_reference_web_agent import (
        build_production_reference_web_agent,
    )
    from agentos.persistence import PostgresSessionSnapshotPersistence

    example = build_production_reference_web_agent(
        backend_verification_records=backend_verification_records(
            {
                "message_queue": "redis://other-deployment/0",
                "session_snapshot_persistence": (
                    "postgresql://other-deployment/agentos"
                ),
            },
        ),
        lease_store=RedisSessionLeaseStore(
            "redis://deployment.example/0",
            client=DeploymentRedisClient(),
        ),
        snapshot_persistence=PostgresSessionSnapshotPersistence(
            "postgresql://deployment.example/agentos",
            connection=DeploymentPostgresConnection(),
        ),
        workspace_isolation_profile=DeploymentWorkspaceIsolationProfile(),
    )
    evidence = example.as_dict()

    assert example.readiness_bundle.accepted is False
    assert "reference_served_backend_binding" in evidence["readiness_bundle"][
        "blocking_checks"
    ]
    binding = readiness_check_evidence(
        evidence,
        "reference_served_backend_binding",
    )
    assert binding["block_production_readiness"] is True
    assert binding["mismatched_backends"] == [
        "message_queue",
        "session_snapshot_persistence",
    ]


def test_reference_snapshot_agent_factory_restores_full_dynamic_session_state() -> None:
    from agentos.compression import CompressionIndex
    from agentos.context import ContextState
    from agentos.examples.production_reference_web_agent import (
        ReferenceSnapshotAgentFactory,
    )
    from agentos.messages import MessageRuntime
    from agentos.persistence import SessionSnapshot
    from agentos.runtime import SessionState

    messages = MessageRuntime()
    messages.append_user("persisted input")
    compression = CompressionIndex()
    compression.record("segment_1", ["msg_1"])
    snapshot = SessionSnapshot(
        session_state=SessionState.from_snapshot(
            id="s1",
            status="active",
            next_turn_number=7,
        ),
        context_state=ContextState(working_state={"goal": "restore"}),
        message_runtime=messages,
        compression_index=compression,
        next_segment_number=3,
    )
    factory = ReferenceSnapshotAgentFactory()

    agent = factory.create_agent(session_id="s1", snapshot=snapshot)
    restored = factory.create_snapshot(session_id="s1", agent=agent)

    assert agent.query_loop.session_state is not None
    assert agent.query_loop.session_state.next_turn_number() == 7
    assert agent.query_loop.context_runtime.snapshot().working_state == {
        "goal": "restore",
    }
    assert restored.session_state.next_turn_number() == 7
    assert restored.context_state.working_state == {"goal": "restore"}
    assert restored.compression_index.snapshot() == {"segment_1": ("msg_1",)}
    assert restored.next_segment_number == 3


def test_production_reference_web_agent_readiness_endpoint_blocks_without_live_backend_evidence() -> None:
    from agentos.examples.production_reference_web_agent import (
        build_reference_app,
    )

    app = build_reference_app(auth_policy=HeaderTokenAuth("reference-token"))

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/ready",
            headers=auth_headers(),
        ),
    )

    assert response_status(sent) == 503
    assert json.loads(response_body(sent)) == {
        "status": "not_ready",
        "checks": {
            "agent_service_reference": "failed",
            "distributed_stream_resume": "failed",
            "distributed_web_session_operations": "ok",
            "production_state_plane": "ok",
            "workspace_execution_isolation": "failed",
            "reference_state_plane_stack": "failed",
            "deployment_live_backend_verification": "failed",
            "production_reference_web_agent": "failed",
        },
    }


def test_production_reference_web_agent_readiness_endpoint_blocks_local_workspace_backend_with_imported_live_backend_evidence() -> None:
    from agentos.channels import RedisSessionLeaseStore
    from agentos.examples.production_reference_web_agent import (
        build_reference_app,
    )
    from agentos.persistence import PostgresSessionSnapshotPersistence

    app = build_reference_app(
        backend_verification_records=backend_verification_records(),
        auth_policy=HeaderTokenAuth("reference-token"),
        lease_store=RedisSessionLeaseStore(
            "redis://deployment.example/0",
            client=DeploymentRedisClient(),
        ),
        snapshot_persistence=PostgresSessionSnapshotPersistence(
            "postgresql://deployment.example/agentos",
            connection=DeploymentPostgresConnection(),
        ),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/ready",
            headers=auth_headers(),
        ),
    )

    assert response_status(sent) == 503
    assert json.loads(response_body(sent)) == {
        "status": "not_ready",
        "checks": {
            "agent_service_reference": "failed",
            "distributed_stream_resume": "ok",
            "distributed_web_session_operations": "ok",
            "production_state_plane": "ok",
            "workspace_execution_isolation": "failed",
            "reference_state_plane_stack": "failed",
            "deployment_live_backend_verification": "ok",
            "production_reference_web_agent": "failed",
        },
    }


def test_production_reference_web_agent_readiness_endpoint_is_ready_with_deployment_workspace_isolation() -> None:
    from agentos.channels import RedisSessionLeaseStore
    from agentos.examples.production_reference_web_agent import (
        build_reference_app,
    )
    from agentos.persistence import PostgresSessionSnapshotPersistence

    app = build_reference_app(
        backend_verification_records=backend_verification_records(),
        auth_policy=HeaderTokenAuth("reference-token"),
        lease_store=RedisSessionLeaseStore(
            "redis://deployment.example/0",
            client=DeploymentRedisClient(),
        ),
        snapshot_persistence=PostgresSessionSnapshotPersistence(
            "postgresql://deployment.example/agentos",
            connection=DeploymentPostgresConnection(),
        ),
        workspace_isolation_profile=DeploymentWorkspaceIsolationProfile(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/ready",
            headers=auth_headers(),
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {
        "status": "ready",
        "checks": {
            "agent_service_reference": "ok",
            "distributed_stream_resume": "ok",
            "distributed_web_session_operations": "ok",
            "production_state_plane": "ok",
            "workspace_execution_isolation": "ok",
            "reference_state_plane_stack": "ok",
            "deployment_live_backend_verification": "ok",
            "production_reference_web_agent": "ok",
        },
    }


def test_production_reference_web_agent_readiness_blocks_demo_runtime_even_with_imported_live_backend_evidence() -> None:
    from agentos.examples.production_reference_web_agent import (
        build_reference_app,
    )

    app = build_reference_app(
        backend_verification_records=backend_verification_records(),
        auth_policy=HeaderTokenAuth("reference-token"),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/ready",
            headers=auth_headers(),
        ),
    )

    assert response_status(sent) == 503
    checks = json.loads(response_body(sent))["checks"]
    assert checks["distributed_stream_resume"] == "failed"
    assert checks["production_reference_web_agent"] == "failed"


def test_production_reference_web_agent_readiness_endpoint_requires_auth_policy() -> None:
    from agentos.examples.production_reference_web_agent import (
        build_reference_app,
    )

    app = build_reference_app()

    sent = asyncio.run(call_asgi(app, method="GET", path="/ready"))

    assert response_status(sent) == 401
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "channel auth policy required",
    }


def test_production_reference_web_agent_main_emits_json_evidence(capsys) -> None:
    from agentos.examples.production_reference_web_agent import main

    exit_code = main([])

    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["example"] == "production_reference_web_agent"
    assert payload["readiness_endpoint"] == "/ready"
    assert payload["mode"] == "demo_without_live_backend_evidence"
    assert payload["readiness_bundle"]["accepted"] is False
    assert payload["planner_primitive"]["pattern"] == "plan-and-execute"
