from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agentos import AgentBuilder
from agentos.channels import (
    AllowAllChannelAuthPolicy,
    AsgiAgentApp,
    DurableAgentSessionProvider,
    InMemoryAgentSessionProvider,
    InMemorySessionLeaseStore,
    InMemorySseEventBuffer,
    InMemorySseTurnControlStore,
)
from agentos.compression import CompressionIndex
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.multi import (
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    InMemoryTeamStore,
    InMemoryTeamUiStreamStore,
    InMemoryTeamWorkerCancellationStore,
    InMemoryTeamWorkerSessionProvider,
    InMemoryTeamWorkerRetryStore,
    SpawnExecutor,
    TaskTable,
    TeamRuntime,
    TeamTools,
    TeamWorkerDaemon,
    TeamWorkerRetryPolicy,
    TeamWorkerRunner,
)
from agentos.providers import FakeProvider
from agentos.runtime import AsyncQueryLoop, QueryLoop
from agentos.runtime.profile import (
    DistributedAgentProfile,
    DistributedTeamRuntimeProfile,
    DistributedWebSessionOperationsProfile,
    DistributedWebRuntimeProfile,
    LocalRuntimeProfile,
    ProductionStatePlaneDeploymentProfile,
    WebRuntimeProfile,
    WorkerProcessLifecycleDeploymentProfile,
)
from agentos.persistence import MemoryPersistence, SessionSnapshot
from agentos.workspace import LocalWorkspaceProvider, WorkspaceRequest
from agentos.runtime import SessionState


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_local_runtime_profile_builds_sync_agent_by_default() -> None:
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
    )

    agent = profile.build_agent()

    assert profile.name == "local"
    assert isinstance(agent.query_loop, QueryLoop)
    assert agent.run("hello").content == "ok"


def test_local_runtime_profile_can_build_async_agent() -> None:
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
        loop_mode="async",
    )

    agent = profile.build_agent()

    assert isinstance(agent.query_loop, AsyncQueryLoop)


def test_local_runtime_profile_resolves_process_workspace(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
        workspace_provider=provider,
        workspace_request=WorkspaceRequest(
            agent_id="agent_a",
            requested_scope="process",
        ),
    )

    assert profile.workspace_handle is not None
    assert profile.workspace_handle.scope == "process"
    assert profile.workspace_handle.root == str(tmp_path)


def test_web_runtime_profile_requires_session_id_for_build_agent() -> None:
    provider = InMemoryAgentSessionProvider(
        lambda session_id: AgentBuilder().provider(FakeProvider(["ok"])).build(),
    )
    profile = WebRuntimeProfile(
        session_provider=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    with pytest.raises(ValueError, match="requires a session_id"):
        profile.build_agent()


def test_web_runtime_profile_builds_agent_from_session_provider() -> None:
    provider = InMemoryAgentSessionProvider(
        lambda session_id: AgentBuilder()
        .provider(FakeProvider([f"ok:{session_id}"]))
        .build(),
    )
    profile = WebRuntimeProfile(
        session_provider=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    agent = profile.build_agent("s1")

    assert agent.run("hello").content == "ok:s1"


def test_web_runtime_profile_exposes_session_readiness_metadata() -> None:
    provider = InMemoryAgentSessionProvider(
        lambda session_id: AgentBuilder().provider(FakeProvider(["ok"])).build(),
    )

    single_process_profile = WebRuntimeProfile(session_provider=provider)
    durable_profile = WebRuntimeProfile(
        session_provider=provider,
        session_lifecycle="durable-session",
    )

    assert single_process_profile.readiness_metadata()["session_lifecycle"] == (
        "single-process"
    )
    assert durable_profile.readiness_metadata()["session_lifecycle"] == (
        "durable-session"
    )


def test_web_runtime_profile_exposes_explicit_workspace(tmp_path: Path) -> None:
    session_provider = InMemoryAgentSessionProvider(
        lambda session_id: AgentBuilder().provider(FakeProvider(["ok"])).build(),
    )
    provider = LocalWorkspaceProvider(base_dir=tmp_path)
    profile = WebRuntimeProfile(
        session_provider=session_provider,
        workspace_provider=provider,
        workspace_request=WorkspaceRequest(
            session_id="session_1",
            requested_scope="session",
        ),
    )

    assert profile.workspace_handle is not None
    assert profile.workspace_handle.workspace_id == "session:session_1"


def test_web_runtime_profile_builds_asgi_app() -> None:
    provider = InMemoryAgentSessionProvider(
        lambda session_id: AgentBuilder().provider(FakeProvider(["web ok"])).build(),
    )
    profile = WebRuntimeProfile(
        session_provider=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )
    app = profile.build_channel_app()

    assert isinstance(app, AsgiAgentApp)

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, object]:
        return {
            "type": "http.request",
            "body": json.dumps({"message": "hello"}).encode("utf-8"),
            "more_body": False,
        }

    asyncio.run(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/sessions/s1/turns",
                "headers": [],
            },
            receive,
            send,
        ),
    )

    body = json.loads(sent[-1]["body"])
    assert body["content"] == "web ok"


class EmptySubagentFactory:
    def create_subagent(self, request: object) -> object:
        return AgentBuilder().provider(FakeProvider(["child"])).build()


class RecordingTeamWorkerAgent:
    def __init__(self) -> None:
        self.continuations = 0

    def run_continuation(self) -> object:
        self.continuations += 1
        return object()


class RecordingTeamWorkerAgentProvider:
    def __init__(self) -> None:
        self.agents: dict[str, RecordingTeamWorkerAgent] = {}

    def get_worker_agent(self, session) -> RecordingTeamWorkerAgent:
        return self.agents.setdefault(
            session.session_id,
            RecordingTeamWorkerAgent(),
        )


class SnapshotFactory:
    def __init__(self) -> None:
        self.responses: dict[str, list[str]] = {"s1": ["node-a", "node-b"]}

    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ):
        provider = FakeProvider(self.responses.setdefault(session_id, ["ok"]))
        context = (
            ContextRuntime(session_id=session_id)
            if snapshot is None
            else ContextRuntime(
                state=snapshot.context_state,
                session_id=session_id,
            )
        )
        messages = snapshot.message_runtime if snapshot is not None else MessageRuntime()
        return (
            AgentBuilder()
            .provider(provider)
            .context_runtime(context)
            .message_runtime(messages)
            .build()
        )

    def create_snapshot(self, *, session_id: str, agent) -> SessionSnapshot:
        query_loop = agent.query_loop
        return SessionSnapshot(
            session_state=query_loop.session_state or SessionState(id=session_id),
            context_state=query_loop.context_runtime.snapshot(),
            message_runtime=query_loop.message_runtime,
            compression_index=CompressionIndex(),
        )


def test_distributed_agent_profile_holds_coordination_primitives() -> None:
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        task_store=TaskTable(),
        message_queue=AgentInbox(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=EmptySubagentFactory(),
    )

    profile = DistributedAgentProfile(
        coordinator=coordinator,
        readiness={"task_store": "ready"},
    )

    assert profile.name == "distributed-agent"
    assert profile.coordinator is coordinator
    assert profile.readiness_checks() == {"task_store": "ready"}
    with pytest.raises(NotImplementedError, match="coordination"):
        profile.build_agent()


def test_distributed_team_runtime_profile_assembles_team_boundaries() -> None:
    message_queue = AgentInbox()
    worker_agent_provider = RecordingTeamWorkerAgentProvider()
    worker_session_provider = InMemoryTeamWorkerSessionProvider(
        id_factory=lambda request: f"session_for_{request.agent_id}",
    )
    ui_stream = InMemoryTeamUiStreamStore()
    profile = DistributedTeamRuntimeProfile(
        store=InMemoryTeamStore(),
        message_queue=message_queue,
        worker_session_provider=worker_session_provider,
        worker_agent_provider=worker_agent_provider,
        retry_policy=TeamWorkerRetryPolicy(max_attempts=2, backoff_seconds=1.0),
        retry_store=InMemoryTeamWorkerRetryStore(),
        cancellation_store=InMemoryTeamWorkerCancellationStore(),
        ui_stream=ui_stream,
        daemon_poll_interval_seconds=0.01,
    )

    runtime = profile.build_team_runtime()
    tools = profile.build_team_tools(owner_agent_id="leader")
    runner = profile.build_worker_runner()
    daemon = profile.build_worker_daemon()

    assert profile.name == "distributed-team"
    assert isinstance(runtime, TeamRuntime)
    assert runtime is profile.team_runtime
    assert isinstance(tools, TeamTools)
    assert isinstance(runner, TeamWorkerRunner)
    assert runner is profile.worker_runner
    assert isinstance(daemon, TeamWorkerDaemon)
    assert daemon.runner is runner
    assert runtime.store is profile.store
    assert runtime.message_queue is message_queue
    assert runtime.worker_session_provider is worker_session_provider
    assert runtime.ui_stream is ui_stream
    with pytest.raises(NotImplementedError, match="team coordination"):
        profile.build_agent()


def test_distributed_team_runtime_profile_processes_worker_message_batch() -> None:
    message_queue = AgentInbox()
    worker_agent_provider = RecordingTeamWorkerAgentProvider()
    profile = DistributedTeamRuntimeProfile(
        store=InMemoryTeamStore(),
        message_queue=message_queue,
        worker_session_provider=InMemoryTeamWorkerSessionProvider(
            id_factory=lambda request: f"session_for_{request.agent_id}",
        ),
        worker_agent_provider=worker_agent_provider,
        ui_stream=InMemoryTeamUiStreamStore(),
    )
    runtime = profile.build_team_runtime()
    daemon = profile.build_worker_daemon()

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        leader_session_id="session_leader",
    )
    runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        capabilities=("research",),
    )
    runtime.say(
        team_id="team_1",
        from_agent_id="leader",
        to_agent_id="worker",
        content="Find source A.",
        kind="instruction",
    )

    results = daemon.run_once()

    assert [result.status for result in results] == ["completed"]
    assert worker_agent_provider.agents["session_for_worker"].continuations == 1
    assert [
        event.kind
        for event in profile.ui_stream.list_events("team_1")
    ] == [
        "team_created",
        "member_added",
        "message_appended",
        "worker_run_completed",
    ]


def test_distributed_team_runtime_profile_readiness_metadata_names_adapters() -> None:
    profile = DistributedTeamRuntimeProfile(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        worker_session_provider=InMemoryTeamWorkerSessionProvider(),
        worker_agent_provider=RecordingTeamWorkerAgentProvider(),
        retry_policy=TeamWorkerRetryPolicy(max_attempts=3),
        retry_store=InMemoryTeamWorkerRetryStore(),
        cancellation_store=InMemoryTeamWorkerCancellationStore(),
        ui_stream=InMemoryTeamUiStreamStore(),
        daemon_poll_interval_seconds=0.25,
    )

    metadata = profile.readiness_metadata()

    assert metadata["team_runtime"] == "TeamRuntime"
    assert metadata["team_store"] == "InMemoryTeamStore"
    assert metadata["message_queue"] == "AgentInbox"
    assert metadata["worker_session_provider"] == (
        "InMemoryTeamWorkerSessionProvider"
    )
    assert metadata["worker_runner"] == "TeamWorkerRunner"
    assert metadata["worker_daemon"] == "TeamWorkerDaemon"
    assert metadata["retry_policy"] == "TeamWorkerRetryPolicy"
    assert metadata["retry_store"] == "InMemoryTeamWorkerRetryStore"
    assert metadata["cancellation_store"] == "InMemoryTeamWorkerCancellationStore"
    assert metadata["ui_stream"] == "InMemoryTeamUiStreamStore"
    assert metadata["daemon_poll_interval_seconds"] == 0.25
    assert "process supervision" in metadata["production_gaps"]
    assert profile.readiness_checks()["profile"] == "distributed-team"


def test_worker_process_lifecycle_deployment_profile_reports_missing_components() -> None:
    profile = WorkerProcessLifecycleDeploymentProfile(
        configured_components=(
            "process_supervisor",
            "restart_policy",
            "health_probe",
        ),
    )

    metadata = profile.readiness_metadata()

    assert metadata["profile"] == "WorkerProcessLifecycleDeploymentProfile"
    assert metadata["probe_name"] == "worker_process_lifecycle"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == (
        "process_supervisor",
        "restart_policy",
        "health_probe",
    )
    assert metadata["missing_components"] == (
        "graceful_shutdown",
        "readiness_probe",
        "scaling_policy",
        "credential_policy",
        "migration_policy",
        "alerting",
        "live_backend_verification",
    )
    assert "TeamWorkerDaemon" in metadata["sdk_owned"]
    assert "PlannerRuntime.scheduler_tick" in metadata["sdk_owned"]
    assert "process supervisor or job runner" in metadata["deployment_owned"]
    assert "horizontal scaling policy" in metadata["deployment_owned"]
    assert profile.readiness_check()["status"] == "failed"


def test_worker_process_lifecycle_deployment_profile_marks_ready_when_components_are_configured() -> None:
    profile = WorkerProcessLifecycleDeploymentProfile(
        configured_components=(
            "process_supervisor",
            "restart_policy",
            "graceful_shutdown",
            "health_probe",
            "readiness_probe",
            "scaling_policy",
            "credential_policy",
            "migration_policy",
            "alerting",
            "live_backend_verification",
        ),
    )

    metadata = profile.readiness_metadata()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert profile.readiness_check()["ok"] is True
    assert profile.readiness_check()["status"] == "ok"


def test_worker_process_lifecycle_deployment_profile_rejects_empty_names() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        WorkerProcessLifecycleDeploymentProfile(probe_name=" ")

    with pytest.raises(ValueError, match="required_components"):
        WorkerProcessLifecycleDeploymentProfile(required_components=())

    with pytest.raises(ValueError, match="configured_components"):
        WorkerProcessLifecycleDeploymentProfile(configured_components=("",))


def test_production_state_plane_deployment_profile_reports_missing_components() -> None:
    profile = ProductionStatePlaneDeploymentProfile(
        configured_components=(
            "agent_registry",
            "message_queue",
            "task_store",
        ),
    )

    metadata = profile.readiness_metadata()

    assert metadata["profile"] == "ProductionStatePlaneDeploymentProfile"
    assert metadata["probe_name"] == "production_state_plane"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == (
        "agent_registry",
        "message_queue",
        "task_store",
    )
    assert metadata["missing_components"] == (
        "plan_store",
        "worker_process_supervisor",
        "session_snapshot_persistence",
        "state_plane_boundary_policy",
        "live_backend_verification",
    )
    metadata_text = repr(metadata)
    assert "NacosAgentRegistryAdapter" in metadata_text
    assert "RedisAgentMessageQueue" in metadata_text
    assert "PostgresTaskStore" in metadata_text
    assert "PostgresPlanStore" in metadata_text
    assert "WorkerProcessSupervisor" in metadata_text
    assert "SessionSnapshotPersistence" in metadata_text
    assert "Registry discovers agents and workers" in metadata_text
    assert "Queue delivers messages and wakeups" in metadata_text
    assert "Truth state lives in stores" in metadata_text
    assert "registry is not task truth" in metadata_text
    assert profile.readiness_check()["status"] == "failed"


def test_production_state_plane_deployment_profile_marks_ready_when_components_are_configured() -> None:
    profile = ProductionStatePlaneDeploymentProfile(
        configured_components=(
            "agent_registry",
            "message_queue",
            "task_store",
            "plan_store",
            "worker_process_supervisor",
            "session_snapshot_persistence",
            "state_plane_boundary_policy",
            "live_backend_verification",
        ),
    )

    metadata = profile.readiness_metadata()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert profile.readiness_check()["ok"] is True
    assert profile.readiness_check()["status"] == "ok"


def test_production_state_plane_deployment_profile_rejects_empty_names() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        ProductionStatePlaneDeploymentProfile(probe_name=" ")

    with pytest.raises(ValueError, match="required_components"):
        ProductionStatePlaneDeploymentProfile(required_components=())

    with pytest.raises(ValueError, match="configured_components"):
        ProductionStatePlaneDeploymentProfile(configured_components=("",))


def test_distributed_web_runtime_profile_assembles_durable_session_provider() -> None:
    profile = DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=MemoryPersistence(),
        owner_id="node-a",
    )

    assert profile.name == "distributed-web"
    assert isinstance(profile.session_provider, DurableAgentSessionProvider)
    assert isinstance(profile.build_channel_app(), AsgiAgentApp)
    assert profile.session_lifecycle == "durable-session"


def test_distributed_web_runtime_profile_wires_stream_buffer_and_lease_heartbeat() -> None:
    shared_buffer = InMemorySseEventBuffer()
    profile = DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=MemoryPersistence(),
        owner_id="node-a",
        lease_ttl_seconds=9.0,
        sse_event_buffer=shared_buffer,
        session_lease_heartbeat_interval_seconds=2.0,
    )

    app = profile.build_channel_app()

    assert isinstance(app, AsgiAgentApp)
    assert app._sse_event_buffer is shared_buffer
    assert app._session_lease_heartbeat_interval_seconds == 2.0
    metadata = profile.readiness_metadata()
    assert metadata["sse_event_buffer"] == "InMemorySseEventBuffer"
    assert metadata["session_lease_heartbeat_interval_seconds"] == 2.0


def test_distributed_web_runtime_profile_wires_sse_turn_control() -> None:
    turn_control = InMemorySseTurnControlStore()
    profile = DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=MemoryPersistence(),
        owner_id="node-a",
        sse_turn_control=turn_control,
        sse_turn_control_owner_id="node-a",
        sse_turn_control_ttl_seconds=8.0,
        sse_turn_control_poll_interval_seconds=0.5,
    )

    app = profile.build_channel_app()
    metadata = profile.readiness_metadata()

    assert isinstance(app, AsgiAgentApp)
    assert app._sse_turn_control is turn_control
    assert app._sse_turn_control_owner_id == "node-a"
    assert app._sse_turn_control_ttl_seconds == 8.0
    assert app._sse_turn_control_poll_interval_seconds == 0.5
    assert metadata["sse_turn_control"] == "InMemorySseTurnControlStore"
    assert metadata["sse_turn_control_owner_id"] == "node-a"
    assert metadata["sse_turn_control_ttl_seconds"] == 8.0
    assert metadata["sse_turn_control_poll_interval_seconds"] == 0.5


def test_distributed_web_runtime_profile_hydrates_session_across_nodes() -> None:
    factory = SnapshotFactory()
    lease_store = InMemorySessionLeaseStore()
    persistence = MemoryPersistence()
    node_a = DistributedWebRuntimeProfile(
        agent_factory=factory,
        lease_store=lease_store,
        snapshot_persistence=persistence,
        owner_id="node-a",
    )
    node_b = DistributedWebRuntimeProfile(
        agent_factory=factory,
        lease_store=lease_store,
        snapshot_persistence=persistence,
        owner_id="node-b",
    )

    agent_a = node_a.build_agent("s1")
    assert agent_a.run("hello").content == "node-a"
    node_a.release_agent("s1", agent_a)

    agent_b = node_b.build_agent("s1")
    try:
        assert [m.content for m in agent_b.query_loop.message_runtime.store.all()] == [
            "hello",
            "node-a",
        ]
        assert agent_b.run("next").content == "node-b"
    finally:
        node_b.release_agent("s1", agent_b)


def test_distributed_web_runtime_profile_readiness_metadata_names_adapters() -> None:
    profile = DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=MemoryPersistence(),
        owner_id="node-a",
    )

    metadata = profile.readiness_metadata()

    assert metadata["session_lifecycle"] == "durable-session"
    assert metadata["session_provider"] == "DurableAgentSessionProvider"
    assert metadata["lease_store"] == "InMemorySessionLeaseStore"
    assert metadata["snapshot_persistence"] == "MemoryPersistence"
    assert "workspace enforcement" in metadata["production_gaps"]
    assert "live backend verification" in metadata["production_gaps"]


def test_distributed_web_runtime_profile_reports_stream_resume_readiness_gaps() -> None:
    profile = DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=MemoryPersistence(),
        owner_id="node-a",
    )

    metadata = profile.readiness_metadata()
    assert profile.readiness_checks is not None
    check = profile.readiness_checks["distributed_stream_resume"]()

    assert metadata["distributed_stream_resume_ready"] is False
    assert metadata["stream_resume_gaps"] == [
        "shared_sse_event_buffer",
        "sse_turn_control",
        "session_lease_heartbeat",
    ]
    assert check["status"] == "failed"
    assert check["ok"] is False
    assert check["missing_components"] == [
        "shared_sse_event_buffer",
        "sse_turn_control",
        "session_lease_heartbeat",
    ]


def test_distributed_web_runtime_profile_marks_stream_resume_ready_when_shared_components_configured() -> None:
    profile = DistributedWebRuntimeProfile(
        agent_factory=SnapshotFactory(),
        lease_store=InMemorySessionLeaseStore(),
        snapshot_persistence=MemoryPersistence(),
        owner_id="node-a",
        sse_event_buffer=InMemorySseEventBuffer(),
        sse_turn_control=InMemorySseTurnControlStore(),
        session_lease_heartbeat_interval_seconds=2.0,
    )

    metadata = profile.readiness_metadata()
    assert profile.readiness_checks is not None
    check = profile.readiness_checks["distributed_stream_resume"]()

    assert metadata["distributed_stream_resume_ready"] is True
    assert metadata["stream_resume_gaps"] == []
    assert check["status"] == "ok"
    assert check["ok"] is True
    assert check["configured_components"] == [
        "shared_sse_event_buffer",
        "sse_turn_control",
        "session_lease_heartbeat",
    ]


def test_distributed_web_session_operations_profile_reports_missing_components() -> None:
    profile = DistributedWebSessionOperationsProfile(
        configured_components=(
            "durable_session_provider",
            "lease_store",
            "snapshot_persistence",
        ),
    )

    metadata = profile.readiness_metadata()

    assert metadata["profile"] == "DistributedWebSessionOperationsProfile"
    assert metadata["probe_name"] == "distributed_web_session_operations"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == (
        "durable_session_provider",
        "lease_store",
        "snapshot_persistence",
    )
    assert metadata["missing_components"] == (
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
    )
    assert "DurableAgentSessionProvider" in metadata["sdk_owned"]
    assert "stale lease recovery policy" in metadata["deployment_owned"]
    metadata_text = repr(metadata).lower()
    assert "redis" not in metadata_text
    assert "postgres" not in metadata_text
    assert profile.readiness_check()["status"] == "failed"


def test_distributed_web_session_operations_profile_marks_ready_when_components_are_configured() -> None:
    profile = DistributedWebSessionOperationsProfile(
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

    metadata = profile.readiness_metadata()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert profile.readiness_check()["ok"] is True
    assert profile.readiness_check()["status"] == "ok"


def test_distributed_web_session_operations_profile_rejects_empty_names() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        DistributedWebSessionOperationsProfile(probe_name=" ")

    with pytest.raises(ValueError, match="required_components"):
        DistributedWebSessionOperationsProfile(required_components=())

    with pytest.raises(ValueError, match="configured_components"):
        DistributedWebSessionOperationsProfile(configured_components=("",))


def test_runtime_profile_does_not_leak_into_query_loop() -> None:
    query_loop = PROJECT_ROOT / "src" / "agentos" / "runtime" / "query_loop.py"
    text = query_loop.read_text(encoding="utf-8")

    assert "runtime.profile" not in text
    assert "agentos.channels" not in text
    assert "agentos.registry" not in text
