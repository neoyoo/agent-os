from __future__ import annotations

import asyncio
import json
from typing import Any
from collections.abc import Mapping
from pathlib import Path

from agentos import AgentBuilder
from agentos.channels import (
    AsgiAgentApp,
    ChannelAuthError,
    InMemorySseEventBuffer,
    InMemorySseTurnControlStore,
    InMemorySessionLeaseStore,
    RateLimitDecision,
)
from agentos.persistence import (
    MemoryPersistence,
    SessionSnapshot,
    SessionSnapshotRecord,
    SnapshotConflictError,
)
from agentos.providers import FakeProvider
from agentos.runtime import DistributedWebRuntimeProfile, SessionState
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.compression import CompressionIndex


async def call_asgi(
    app,
    *,
    method: str = "GET",
    path: str = "/",
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[dict[str, Any]]:
    messages = [{"type": "http.request", "body": body, "more_body": False}]
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


class SnapshotFactory:
    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ):
        if snapshot is not None:
            message_runtime = snapshot.message_runtime
        else:
            message_runtime = MessageRuntime()
        return (
            AgentBuilder()
            .provider(FakeProvider([f"ok:{session_id}"]))
            .message_runtime(message_runtime)
            .build()
        )

    def create_snapshot(self, *, session_id: str, agent) -> SessionSnapshot:
        return SessionSnapshot(
            session_state=SessionState(id=session_id),
            context_state=ContextRuntime().state,
            message_runtime=agent.query_loop.message_runtime,
            compression_index=CompressionIndex(),
        )


class HeaderTokenAuth:
    def __init__(self, token: str) -> None:
        self._token = token

    def authorize(self, headers: Mapping[str, str]) -> None:
        if headers.get("x-agentos-token") != self._token:
            raise ChannelAuthError("missing token")


class DenySecondRateLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def check(self, key: str) -> RateLimitDecision:
        self.calls += 1
        return RateLimitDecision(allowed=self.calls == 1, retry_after_seconds=7)


class FencedMemoryPersistence(MemoryPersistence):
    def __init__(self) -> None:
        super().__init__()
        self.revisions: dict[str, int] = {}

    def load_record(self, session_id: str) -> SessionSnapshotRecord:
        return SessionSnapshotRecord(
            snapshot=self.load(session_id),
            revision=self.revisions[session_id],
        )

    def save_if_lease_owned(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
        lease,
        lease_store,
    ) -> SessionSnapshotRecord:
        session_id = snapshot.session_state.id
        lease_store.ensure_owned(lease)
        current_revision = self.revisions.get(session_id, 0)
        if current_revision != expected_revision:
            raise SnapshotConflictError(
                f"snapshot revision conflict: {session_id}",
            )
        super().save(snapshot)
        lease_store.ensure_owned(lease)
        revision = current_revision + 1
        self.revisions[session_id] = revision
        return SessionSnapshotRecord(snapshot=snapshot, revision=revision)


def distributed_profile(*, auth_policy: object | None = None) -> DistributedWebRuntimeProfile:
    return DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=FencedMemoryPersistence(),
        owner_id="node-a",
        auth_policy=auth_policy,
    )


def test_agent_service_reference_builds_asgi_app_and_readiness_endpoint() -> None:
    from agentos.service import (
        AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS,
        AgentServiceReference,
        AgentServiceReferenceProfile,
    )
    from agentos.deployment import ProductionStatePlaneDeploymentProfile
    from agentos.runtime import DistributedWebSessionOperationsProfile
    from agentos.workspace import WorkspaceExecutionIsolationProfile

    service_profile = AgentServiceReferenceProfile(
        configured_components=tuple(
            component
            for component in AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS
            if component != "live_backend_verification"
        ),
    )
    service = AgentServiceReference(
        runtime_profile=distributed_profile(
            auth_policy=HeaderTokenAuth("ready-token"),
        ),
        service_profile=service_profile,
        distributed_session_profile=DistributedWebSessionOperationsProfile(
            configured_components=(
                "durable_session_provider",
                "lease_store",
                "snapshot_persistence",
            ),
        ),
        state_plane_profile=ProductionStatePlaneDeploymentProfile(
            configured_components=("agent_registry", "message_queue"),
        ),
        workspace_isolation_profile=WorkspaceExecutionIsolationProfile(
            configured_components=(
                "workspace_policy",
                "tool_path_sandbox",
                "capability_allowlist",
            ),
        ),
    )

    app = service.build_asgi_app()
    metadata = service.readiness_metadata()

    assert isinstance(app, AsgiAgentApp)
    assert metadata["profile"] == "AgentServiceReference"
    assert metadata["runtime_profile"] == "DistributedWebRuntimeProfile"
    assert metadata["session_provider"] == "DurableAgentSessionProvider"
    assert metadata["workspace_execution_backend"] is None
    assert metadata["service_profile"]["missing_components"] == [
        "live_backend_verification",
    ]
    assert "DistributedWebSessionOperationsProfile" in repr(metadata)
    assert "ProductionStatePlaneDeploymentProfile" in repr(metadata)
    assert "WorkspaceExecutionIsolationProfile" in repr(metadata)
    assert json.loads(json.dumps(metadata)) == metadata

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/ready",
            headers=[(b"x-agentos-token", b"ready-token")],
        ),
    )

    assert response_status(sent) == 503
    assert json.loads(response_body(sent)) == {
        "status": "not_ready",
            "checks": {
                "agent_service_reference": "failed",
                "distributed_stream_resume": "failed",
                "distributed_web_session_operations": "failed",
                "production_state_plane": "failed",
                "workspace_execution_isolation": "failed",
        },
    }


def test_agent_service_reference_preserves_distributed_channel_settings() -> None:
    from agentos.service import AgentServiceReference

    shared_buffer = InMemorySseEventBuffer()
    turn_control = InMemorySseTurnControlStore()
    runtime_profile = DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=MemoryPersistence(),
        owner_id="node-a",
        auth_policy=HeaderTokenAuth("ready-token"),
        sse_event_buffer=shared_buffer,
        sse_turn_control=turn_control,
        sse_turn_control_owner_id="node-a",
        sse_turn_control_ttl_seconds=8.0,
        sse_turn_control_poll_interval_seconds=0.5,
        session_lease_heartbeat_interval_seconds=2.0,
    )
    service = AgentServiceReference(
        runtime_profile=runtime_profile,
        readiness_checks={"custom": lambda: {"status": "ok", "ok": True}},
    )

    app = service.build_asgi_app()

    assert isinstance(app, AsgiAgentApp)
    assert app._sse_event_buffer is shared_buffer
    assert app._sse_turn_control is turn_control
    assert app._sse_turn_control_owner_id == "node-a"
    assert app._sse_turn_control_ttl_seconds == 8.0
    assert app._sse_turn_control_poll_interval_seconds == 0.5
    assert app._session_lease_heartbeat_interval_seconds == 2.0

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/ready",
            headers=[(b"x-agentos-token", b"ready-token")],
        ),
    )

    assert response_status(sent) == 503
    assert json.loads(response_body(sent)) == {
        "status": "not_ready",
            "checks": {
                "agent_service_reference": "failed",
                "distributed_stream_resume": "failed",
                "custom": "ok",
            },
        }


def test_agent_service_reference_injects_auth_and_rate_limit_hooks() -> None:
    from agentos.service import AgentServiceReference

    limiter = DenySecondRateLimiter()
    service = AgentServiceReference(
        runtime_profile=distributed_profile(),
        auth_policy=HeaderTokenAuth("secret-token"),
        rate_limiter=limiter,
    )
    app = service.build_asgi_app()

    unauthorized = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/s1/turns",
            body=b'{"message":"hello"}',
        ),
    )
    allowed = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/s1/turns",
            headers=[(b"x-agentos-token", b"secret-token")],
            body=b'{"message":"hello"}',
        ),
    )
    limited = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/s2/turns",
            headers=[(b"x-agentos-token", b"secret-token")],
            body=b'{"message":"hello"}',
        ),
    )

    assert response_status(unauthorized) == 401
    assert response_status(allowed) == 200
    assert response_status(limited) == 429
    assert json.loads(response_body(limited)) == {
        "status": "failed",
        "error": "rate limit exceeded",
    }


def test_agent_service_reference_readiness_evidence_redacts_secret_like_values(
    tmp_path: Path,
) -> None:
    from agentos.service import AgentServiceReference
    from agentos.workspace import LocalWorkspaceExecutionBackend

    service = AgentServiceReference(
        runtime_profile=distributed_profile(),
        workspace_execution_backend=LocalWorkspaceExecutionBackend(),
        metadata={
            "service": "customer-support",
            "credential": "raw-secret",
            "nested": {"api_token": "raw-token", "zone": "az-a"},
            "path": tmp_path,
        },
    )

    evidence = service.readiness_metadata()
    encoded = json.dumps(evidence)

    assert evidence["workspace_execution_backend"] == "LocalWorkspaceExecutionBackend"
    assert evidence["metadata"] == {
        "service": "customer-support",
        "credential": "<redacted>",
        "nested": {"api_token": "<redacted>", "zone": "az-a"},
        "path": str(tmp_path),
    }
    assert "raw-secret" not in encoded
    assert "raw-token" not in encoded
