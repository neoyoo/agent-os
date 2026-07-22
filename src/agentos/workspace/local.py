from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from agentos._sync_work import run_sync
from agentos.workspace.models import (
    WorkspaceExecutionRequest,
    WorkspaceExecutionResult,
    WorkspaceHandle,
    WorkspaceRequest,
    WorkspaceScope,
)
from agentos.workspace.policies import (
    WorkspaceExecutionPolicy,
    WorkspacePolicyError,
    _resolve_policy_path,
)


_WINDOWS_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    },
)


@dataclass(slots=True)
class LocalWorkspaceExecutionBackend:
    """Reference local subprocess backend for trusted development workspaces."""

    policy: WorkspaceExecutionPolicy = field(default_factory=WorkspaceExecutionPolicy)
    clock: object | None = None
    inherit_environment: bool = False

    def run(self, request: WorkspaceExecutionRequest) -> WorkspaceExecutionResult:
        """Run argv inside the request workspace without shell parsing."""

        cwd = self.policy.ensure_request_allowed(request)
        started_at = self._now()
        try:
            completed = subprocess.run(
                request.command,
                cwd=str(cwd),
                env=self._process_env(request),
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return self._result(
                request,
                cwd=cwd,
                started_at=started_at,
                finished_at=self._now(),
                exit_code=None,
                stdout=_decode_timeout_output(exc.stdout),
                stderr=_decode_timeout_output(exc.stderr),
                timed_out=True,
                error="timeout",
            )
        except OSError as exc:
            return self._result(
                request,
                cwd=cwd,
                started_at=started_at,
                finished_at=self._now(),
                exit_code=None,
                stdout="",
                stderr="",
                error=str(exc) or exc.__class__.__name__,
            )
        return self._result(
            request,
            cwd=cwd,
            started_at=started_at,
            finished_at=self._now(),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    async def async_run(
        self,
        request: WorkspaceExecutionRequest,
    ) -> WorkspaceExecutionResult:
        """Run one request in a worker thread for async callers."""

        return await run_sync(self.run, request)

    def _result(
        self,
        request: WorkspaceExecutionRequest,
        *,
        cwd: Path,
        started_at: float,
        finished_at: float,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        timed_out: bool = False,
        error: str | None = None,
    ) -> WorkspaceExecutionResult:
        metadata = dict(request.metadata)
        if self.inherit_environment:
            metadata.setdefault("inherits_host_environment", True)
            metadata.setdefault(
                "environment_inheritance_risk",
                "host environment inherited by local reference backend",
            )
        return WorkspaceExecutionResult(
            backend=self.__class__.__name__,
            workspace_id=request.workspace.workspace_id,
            workspace_scope=request.workspace.scope,
            command=tuple(request.command),
            capability=request.capability,
            cwd=str(cwd),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            env_keys=self._env_keys(request),
            metadata=metadata,
            started_at=started_at,
            finished_at=finished_at,
            timed_out=timed_out,
            error=error,
        )

    def _process_env(self, request: WorkspaceExecutionRequest) -> dict[str, str]:
        env = os.environ.copy() if self.inherit_environment else {}
        env.update({key: str(value) for key, value in request.env.items()})
        return env

    def _env_keys(self, request: WorkspaceExecutionRequest) -> tuple[str, ...]:
        keys = set(os.environ) if self.inherit_environment else set()
        keys.update(str(key) for key in request.env)
        return tuple(sorted(keys))

    def _now(self) -> float:
        if callable(self.clock):
            return float(self.clock())
        return time.time()


@dataclass(slots=True)
class LocalWorkspaceProvider:
    """用于本地开发和确定性测试的 workspace provider。"""

    base_dir: Path | str | None = None
    create: bool = False

    def resolve_workspace(self, request: WorkspaceRequest) -> WorkspaceHandle:
        scope = request.requested_scope
        workspace_id = self._workspace_id(scope, request)
        root = self._root_for(scope, workspace_id)
        metadata = {
            key: value
            for key, value in {
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "session_id": request.session_id,
                "team_id": request.team_id,
                "task_id": request.task_id,
            }.items()
            if value is not None
        }
        return WorkspaceHandle(
            workspace_id=workspace_id,
            scope=scope,
            root=str(root) if root is not None else None,
            metadata=metadata,
        )

    def narrow_workspace(
        self,
        parent: WorkspaceHandle,
        *,
        child_id: str,
        scope: WorkspaceScope = "task",
    ) -> WorkspaceHandle:
        safe_child_id = _validate_workspace_segment(child_id, "child_id")
        root = None
        if parent.root is not None:
            parent_root = _resolve_policy_path(Path(parent.root))
            root = _resolve_policy_path(parent_root / f"{scope}s" / safe_child_id)
            if not root.is_relative_to(parent_root):
                raise WorkspacePolicyError(
                    "child workspace root escapes parent workspace root",
                )
            if self.create:
                root.mkdir(parents=True, exist_ok=True)
        return WorkspaceHandle(
            workspace_id=f"{scope}:{safe_child_id}",
            scope=scope,
            root=str(root) if root is not None else None,
            parent_workspace_id=parent.workspace_id,
            metadata={"child_id": safe_child_id},
        )

    def _workspace_id(
        self,
        scope: WorkspaceScope,
        request: WorkspaceRequest,
    ) -> str:
        value_by_scope = {
            "process": request.agent_id or "default",
            "agent": request.agent_id,
            "user": request.user_id,
            "session": request.session_id,
            "team": request.team_id,
            "task": request.task_id,
        }
        field_by_scope = {
            "process": "agent_id",
            "agent": "agent_id",
            "user": "user_id",
            "session": "session_id",
            "team": "team_id",
            "task": "task_id",
        }
        value = value_by_scope[scope]
        if value is None:
            raise WorkspacePolicyError(f"{scope}_id is required")
        return f"{scope}:{_validate_workspace_segment(value, field_by_scope[scope])}"

    def _root_for(self, scope: WorkspaceScope, workspace_id: str) -> Path | None:
        base = _resolve_policy_path(
            Path.cwd() if self.base_dir is None else Path(self.base_dir),
        )
        if scope == "process":
            root = base
        else:
            _prefix, value = workspace_id.split(":", 1)
            root = _resolve_policy_path(base / f"{scope}s" / value)
        if not root.is_relative_to(base):
            raise WorkspacePolicyError("workspace root escapes provider base directory")
        if self.create:
            root.mkdir(parents=True, exist_ok=True)
        return root


def _validate_workspace_segment(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise WorkspacePolicyError(f"invalid {field_name}: must be a string")
    if value == "" or value != value.strip():
        raise WorkspacePolicyError(f"invalid {field_name}: must be non-empty")
    if value in {".", ".."}:
        raise WorkspacePolicyError(f"invalid {field_name}: path segment not allowed")
    if "\x00" in value or "/" in value or "\\" in value or ":" in value:
        raise WorkspacePolicyError(f"invalid {field_name}: path separator not allowed")
    if Path(value).is_absolute():
        raise WorkspacePolicyError(f"invalid {field_name}: absolute path not allowed")
    windows_stem = value.rstrip(" .").split(".", 1)[0].lower()
    if windows_stem in _WINDOWS_RESERVED_PATH_SEGMENTS:
        raise WorkspacePolicyError(
            f"invalid {field_name}: reserved path segment not allowed",
        )
    return value


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


__all__ = ["LocalWorkspaceExecutionBackend", "LocalWorkspaceProvider"]
