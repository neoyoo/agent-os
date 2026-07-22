from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from agentos.workspace import (
    LocalWorkspaceProvider,
    WorkspaceHandle,
    WorkspacePolicy,
    WorkspacePolicyError,
    WorkspaceRequest,
)


def test_workspace_handle_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="invalid workspace scope"):
        WorkspaceHandle("workspace_1", "unknown")  # type: ignore[arg-type]


def test_workspace_handle_copies_and_freezes_metadata() -> None:
    metadata = {"session_id": "session_1"}
    handle = WorkspaceHandle(
        workspace_id="workspace_1",
        scope="session",
        metadata=metadata,
    )

    metadata["session_id"] = "session_tampered"

    assert handle.metadata == {"session_id": "session_1"}
    with pytest.raises(TypeError):
        handle.metadata["session_id"] = "session_other"  # type: ignore[index]


@pytest.mark.parametrize(
    "metadata",
    (
        {1: "session_1"},
        {"session_id": 1},
        {"session_id": ["session_1"]},
    ),
)
def test_workspace_handle_rejects_non_string_metadata(
    metadata: dict[object, object],
) -> None:
    with pytest.raises(TypeError, match="metadata must contain string keys and values"):
        WorkspaceHandle(
            workspace_id="workspace_1",
            scope="session",
            metadata=metadata,  # type: ignore[arg-type]
        )


def test_workspace_handle_supports_deepcopy_and_asdict() -> None:
    handle = WorkspaceHandle(
        workspace_id="workspace_1",
        scope="session",
        metadata={"session_id": "session_1"},
    )

    copied = deepcopy(handle)
    serialized = asdict(handle)

    assert copied == handle
    assert copied.metadata == {"session_id": "session_1"}
    assert serialized == {
        "workspace_id": "workspace_1",
        "scope": "session",
        "root": None,
        "parent_workspace_id": None,
        "metadata": {"session_id": "session_1"},
    }


def test_local_workspace_provider_resolves_process_workspace(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)

    handle = provider.resolve_workspace(
        WorkspaceRequest(agent_id="agent_a", requested_scope="process"),
    )

    assert handle.workspace_id == "process:agent_a"
    assert handle.scope == "process"
    assert handle.root == str(tmp_path)


def test_local_workspace_provider_resolves_session_workspace(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)

    handle = provider.resolve_workspace(
        WorkspaceRequest(
            agent_id="agent_a",
            user_id="user_1",
            session_id="session_1",
            requested_scope="session",
        ),
    )

    assert handle.workspace_id == "session:session_1"
    assert handle.scope == "session"
    assert handle.root == str(tmp_path / "sessions" / "session_1")
    assert handle.metadata == {
        "agent_id": "agent_a",
        "user_id": "user_1",
        "session_id": "session_1",
    }


@pytest.mark.parametrize(
    ("field_name", "workspace_request"),
    [
        (
            "session_id",
            WorkspaceRequest(session_id="../escape", requested_scope="session"),
        ),
        (
            "session_id",
            WorkspaceRequest(session_id="nested/session", requested_scope="session"),
        ),
        (
            "session_id",
            WorkspaceRequest(session_id=r"nested\session", requested_scope="session"),
        ),
        (
            "task_id",
            WorkspaceRequest(task_id="..", requested_scope="task"),
        ),
        (
            "team_id",
            WorkspaceRequest(team_id=" ", requested_scope="team"),
        ),
        (
            "session_id",
            WorkspaceRequest(session_id="CON", requested_scope="session"),
        ),
        (
            "task_id",
            WorkspaceRequest(task_id="aux.txt", requested_scope="task"),
        ),
    ],
)
def test_local_workspace_provider_rejects_path_like_workspace_ids(
    tmp_path: Path,
    field_name: str,
    workspace_request: WorkspaceRequest,
) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path, create=True)

    with pytest.raises(WorkspacePolicyError, match=f"invalid {field_name}"):
        provider.resolve_workspace(workspace_request)


def test_local_workspace_provider_rejects_path_like_child_ids(
    tmp_path: Path,
) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path, create=True)
    parent = provider.resolve_workspace(
        WorkspaceRequest(session_id="session_1", requested_scope="session"),
    )

    with pytest.raises(WorkspacePolicyError, match="invalid child_id"):
        provider.narrow_workspace(parent, child_id="../task_1", scope="task")


def test_local_workspace_provider_rejects_reserved_child_id(
    tmp_path: Path,
) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path, create=True)
    parent = provider.resolve_workspace(
        WorkspaceRequest(session_id="session_1", requested_scope="session"),
    )

    with pytest.raises(WorkspacePolicyError, match="invalid child_id"):
        provider.narrow_workspace(parent, child_id="NUL", scope="task")


def test_workspace_policy_allows_narrowing_but_rejects_broadening(
    tmp_path: Path,
) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)
    policy = WorkspacePolicy()
    parent = provider.resolve_workspace(
        WorkspaceRequest(session_id="session_1", requested_scope="session"),
    )

    child = provider.narrow_workspace(parent, child_id="task_1", scope="task")

    assert child.scope == "task"
    assert child.parent_workspace_id == parent.workspace_id
    policy.ensure_child_workspace_allowed(parent, child)

    wider = provider.narrow_workspace(child, child_id="session_2", scope="session")
    with pytest.raises(WorkspacePolicyError, match="cannot broaden workspace"):
        policy.ensure_child_workspace_allowed(child, wider)


def test_workspace_policy_rejects_child_root_outside_parent_root(
    tmp_path: Path,
) -> None:
    policy = WorkspacePolicy()
    parent = WorkspaceHandle(
        workspace_id="session:one",
        scope="session",
        root=str(tmp_path / "parent"),
    )
    child = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path / "outside" / "tasks" / "one"),
        parent_workspace_id=parent.workspace_id,
    )

    with pytest.raises(WorkspacePolicyError, match="escapes parent workspace root"):
        policy.ensure_child_workspace_allowed(parent, child)


def test_workspace_policy_rejects_child_root_without_parent_root() -> None:
    policy = WorkspacePolicy()
    parent = WorkspaceHandle(
        workspace_id="session:remote",
        scope="session",
        root=None,
    )
    child = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root="/tmp/task-one",
        parent_workspace_id=parent.workspace_id,
    )

    with pytest.raises(WorkspacePolicyError, match="parent workspace root"):
        policy.ensure_child_workspace_allowed(parent, child)


def test_workspace_execution_isolation_profile_reports_missing_components() -> None:
    from agentos.workspace import WorkspaceExecutionIsolationProfile

    profile = WorkspaceExecutionIsolationProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "WorkspaceExecutionIsolationProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "workspace_policy",
        "tool_path_sandbox",
        "capability_allowlist",
        "execution_backend",
        "process_isolation",
        "resource_limits",
        "network_policy",
        "audit_logging",
    }
    assert "WorkspaceToolSandboxPolicy" in metadata["sdk_owned"]
    assert "OS/container sandboxing" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_workspace_execution_isolation_profile_marks_ready_when_components_are_configured() -> None:
    from agentos.workspace import WorkspaceExecutionIsolationProfile

    profile = WorkspaceExecutionIsolationProfile(
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

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_workspace_execution_isolation_profile_rejects_empty_names() -> None:
    from agentos.workspace import WorkspaceExecutionIsolationProfile

    with pytest.raises(ValueError, match="probe_name"):
        WorkspaceExecutionIsolationProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        WorkspaceExecutionIsolationProfile(configured_components=("",))


def test_workspace_execution_request_rejects_shell_strings(
    tmp_path: Path,
) -> None:
    from agentos.workspace import WorkspaceExecutionRequest

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )

    with pytest.raises(ValueError, match="command must be an argv tuple"):
        WorkspaceExecutionRequest(
            workspace=workspace,
            command="python -c print('unsafe')",  # type: ignore[arg-type]
            capability="process.exec",
        )


def test_local_workspace_execution_backend_runs_inside_workspace(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=("python", "-c", "from pathlib import Path; print(Path.cwd())"),
            capability="process.exec",
        ),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == str(tmp_path)
    assert result.stderr == ""
    evidence = result.to_evidence()
    assert evidence["backend"] == "LocalWorkspaceExecutionBackend"
    assert evidence["workspace_id"] == "task:one"
    assert evidence["workspace_scope"] == "task"
    assert evidence["command"] == (
        "python",
        "-c",
        "from pathlib import Path; print(Path.cwd())",
    )
    assert evidence["exit_code"] == 0
    assert evidence["env_keys"] == ()
    assert evidence["stdout_bytes"] > 0
    assert evidence["stderr_bytes"] == 0
    assert "python" not in evidence["metadata"]


def test_local_workspace_execution_backend_rejects_cwd_escape(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionError,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path / "workspace"),
    )
    backend = LocalWorkspaceExecutionBackend()

    with pytest.raises(WorkspaceExecutionError, match="cwd escapes workspace root"):
        backend.run(
            WorkspaceExecutionRequest(
                workspace=workspace,
                command=("python", "-c", "print('nope')"),
                capability="process.exec",
                cwd=str(tmp_path),
            ),
        )


def test_local_workspace_execution_backend_rejects_capability_not_allowed(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionError,
        WorkspaceExecutionPolicy,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend(
        policy=WorkspaceExecutionPolicy(
            allowed_capabilities=frozenset({"filesystem.read"}),
        ),
    )

    with pytest.raises(WorkspaceExecutionError, match="capability not allowed"):
        backend.run(
            WorkspaceExecutionRequest(
                workspace=workspace,
                command=("python", "-c", "print('blocked')"),
                capability="process.exec",
            ),
        )


def test_local_workspace_execution_backend_redacts_env_values_in_evidence(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=("python", "-c", "import os; print(os.environ['SECRET_TOKEN'])"),
            capability="process.exec",
            env={"SECRET_TOKEN": "super-secret"},
        ),
    )

    evidence = result.to_evidence()
    assert result.stdout.strip() == "super-secret"
    assert evidence["env_keys"] == ("SECRET_TOKEN",)
    assert "super-secret" not in str(evidence)


def test_workspace_execution_result_redacts_secret_command_arguments_in_evidence(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=(
                "python",
                "-c",
                "print('ok')",
                "--token",
                "raw-token",
                "--api-key=raw-api-key",
            ),
            capability="process.exec",
        ),
    )

    evidence = result.to_evidence()
    assert result.command == (
        "python",
        "-c",
        "print('ok')",
        "--token",
        "raw-token",
        "--api-key=raw-api-key",
    )
    assert evidence["command"] == (
        "python",
        "-c",
        "print('ok')",
        "--token",
        "<redacted>",
        "--api-key=<redacted>",
    )
    assert "raw-token" not in str(evidence)
    assert "raw-api-key" not in str(evidence)


def test_workspace_execution_result_redacts_secret_like_metadata_in_evidence(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=("python", "-c", "print('ok')"),
            capability="process.exec",
            metadata={
                "api_token": "super-secret-token",
                "nested": {"password": "super-secret-password"},
                "safe": "visible",
            },
        ),
    )

    evidence = result.to_evidence()
    assert evidence["metadata"]["api_token"] == "<redacted>"
    assert evidence["metadata"]["nested"]["password"] == "<redacted>"
    assert evidence["metadata"]["safe"] == "visible"
    assert "super-secret" not in str(evidence)


def test_workspace_execution_result_redacts_secret_patterns_in_metadata_values(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=("python", "-c", "print('ok')"),
            capability="process.exec",
            metadata={
                "note": "Authorization: Bearer raw-token",
                "database": "postgres://user:raw-password@db/app",
                "nested": ["api_key=sk-live-secret", {"safe": "visible"}],
            },
        ),
    )

    evidence = result.to_evidence()

    assert evidence["metadata"]["note"] == "Authorization: Bearer <redacted>"
    assert evidence["metadata"]["database"] == "postgres://<redacted>@db/app"
    assert evidence["metadata"]["nested"][0] == "api_key=<redacted>"
    assert evidence["metadata"]["nested"][1]["safe"] == "visible"
    assert "raw-token" not in str(evidence)
    assert "raw-password" not in str(evidence)
    assert "sk-live-secret" not in str(evidence)


def test_local_workspace_execution_backend_does_not_inherit_host_environment_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    monkeypatch.setenv("AGENTOS_HOST_SECRET", "do-not-leak")
    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=(
                "python",
                "-c",
                "import os; print(os.environ.get('AGENTOS_HOST_SECRET', 'missing'))",
            ),
            capability="process.exec",
        ),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "missing"
    assert result.to_evidence()["env_keys"] == ()


def test_local_workspace_execution_backend_can_explicitly_inherit_host_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    monkeypatch.setenv("AGENTOS_HOST_VALUE", "available")
    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend(inherit_environment=True)

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=(
                "python",
                "-c",
                "import os; print(os.environ['AGENTOS_HOST_VALUE'])",
            ),
            capability="process.exec",
        ),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "available"


def test_local_workspace_execution_backend_marks_inherited_environment_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    monkeypatch.setenv("AGENTOS_HOST_SECRET", "do-not-record")
    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend(inherit_environment=True)

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=("python", "-c", "print('ok')"),
            capability="process.exec",
            env={"AGENTOS_REQUEST_TOKEN": "also-do-not-record"},
        ),
    )

    evidence = result.to_evidence()
    assert evidence["metadata"]["inherits_host_environment"] is True
    assert evidence["metadata"]["environment_inheritance_risk"] == (
        "host environment inherited by local reference backend"
    )
    assert "AGENTOS_HOST_SECRET" in evidence["env_keys"]
    assert "AGENTOS_REQUEST_TOKEN" in evidence["env_keys"]
    assert "do-not-record" not in str(evidence)
    assert "also-do-not-record" not in str(evidence)


def test_local_workspace_execution_backend_records_timeout_evidence(
    tmp_path: Path,
) -> None:
    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    result = backend.run(
        WorkspaceExecutionRequest(
            workspace=workspace,
            command=("python", "-c", "import time; time.sleep(1)"),
            capability="process.exec",
            timeout_seconds=0.01,
        ),
    )

    assert result.timed_out is True
    assert result.exit_code is None
    assert result.error == "timeout"
    assert result.to_evidence()["timed_out"] is True


def test_local_workspace_execution_backend_supports_async_run(
    tmp_path: Path,
) -> None:
    import asyncio

    from agentos.workspace import (
        LocalWorkspaceExecutionBackend,
        WorkspaceExecutionRequest,
    )

    workspace = WorkspaceHandle(
        workspace_id="task:one",
        scope="task",
        root=str(tmp_path),
    )
    backend = LocalWorkspaceExecutionBackend()

    async def run() -> object:
        return await backend.async_run(
            WorkspaceExecutionRequest(
                workspace=workspace,
                command=("python", "-c", "print('async ok')"),
                capability="process.exec",
            ),
        )

    result = asyncio.run(run())

    assert result.stdout.strip() == "async ok"
