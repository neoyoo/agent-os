from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Protocol

from agentos._redaction import (
    is_secret_like_key,
    redact_command_argv,
    redact_secret_patterns,
)


WorkspaceScope = Literal["process", "agent", "user", "session", "team", "task"]

_SCOPE_RANK: dict[WorkspaceScope, int] = {
    "process": 5,
    "user": 4,
    "agent": 3,
    "team": 2,
    "session": 1,
    "task": 0,
}
WORKSPACE_EXECUTION_ISOLATION_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "workspace_policy",
    "tool_path_sandbox",
    "capability_allowlist",
    "execution_backend",
    "process_isolation",
    "resource_limits",
    "network_policy",
    "audit_logging",
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


class WorkspaceExecutionError(PermissionError):
    """Raised when a workspace execution backend rejects a request."""


class WorkspacePolicyError(PermissionError):
    """workspace 策略拒绝请求时抛出。"""


@dataclass(frozen=True, slots=True)
class WorkspaceExecutionIsolationProfile:
    """Deployment-facing readiness contract for workspace execution isolation."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        WORKSPACE_EXECUTION_ISOLATION_REQUIRED_COMPONENTS
    )
    probe_name: str = "workspace_execution_isolation"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_component_names(
            self.configured_components,
            field_name="configured_components",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required isolation components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for execution isolation."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "WorkspaceHandle",
                "WorkspaceProvider",
                "WorkspacePolicy",
                "scope narrowing",
                "WorkspaceToolSandboxPolicy",
                "ToolPathSandboxRule",
                "path escape pre-check",
                "tool capability pre-check",
            ),
            "deployment_owned": (
                "OS/container sandboxing",
                "process isolation",
                "filesystem mount policy",
                "network egress policy",
                "CPU and memory limits",
                "secret redaction",
                "audit logging backend",
                "sandbox image/runtime patching",
                "live sandbox backend verification",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    """执行工作空间的稳定引用。"""

    workspace_id: str
    scope: WorkspaceScope
    root: str | None = None
    parent_workspace_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkspaceRequest:
    """解析 agent、session 或 task 工作空间所需的输入。"""

    agent_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    team_id: str | None = None
    task_id: str | None = None
    requested_scope: WorkspaceScope = "session"


class WorkspaceProvider(Protocol):
    """为 profile 和 task 解析执行工作空间。"""

    def resolve_workspace(self, request: WorkspaceRequest) -> WorkspaceHandle:
        """返回 profile、session 或 task 对应的 workspace handle。"""

    def narrow_workspace(
        self,
        parent: WorkspaceHandle,
        *,
        child_id: str,
        scope: WorkspaceScope = "task",
    ) -> WorkspaceHandle:
        """返回不扩大父级访问范围的子 workspace。"""


@dataclass(frozen=True, slots=True)
class WorkspaceExecutionRequest:
    """Workspace-bound command request for pluggable execution backends."""

    workspace: WorkspaceHandle
    command: tuple[str, ...]
    capability: str
    cwd: str | None = None
    timeout_seconds: float | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.command, tuple):
            raise ValueError("command must be an argv tuple")
        if not self.command:
            raise ValueError("command must not be empty")
        if any(not item.strip() for item in self.command):
            raise ValueError("command must not contain empty values")
        if not self.capability.strip():
            raise ValueError("capability must not be empty")
        if self.cwd is not None and not self.cwd.strip():
            raise ValueError("cwd must not be empty")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if any(not key.strip() for key in self.env):
            raise ValueError("env must not contain empty names")


@dataclass(frozen=True, slots=True)
class WorkspaceExecutionResult:
    """JSON-auditable result from one workspace execution request."""

    backend: str
    workspace_id: str
    workspace_scope: WorkspaceScope
    command: tuple[str, ...]
    capability: str
    cwd: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    env_keys: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None
    timed_out: bool = False
    error: str | None = None

    def to_evidence(self) -> dict[str, object]:
        """Return JSON-safe execution evidence without environment values."""

        return {
            "backend": self.backend,
            "workspace_id": self.workspace_id,
            "workspace_scope": self.workspace_scope,
            "command": redact_command_argv(self.command),
            "capability": self.capability,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "env_keys": self.env_keys,
            "metadata": _json_safe_mapping(self.metadata),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timed_out": self.timed_out,
            "error": self.error,
            "stdout_bytes": len(self.stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(self.stderr.encode("utf-8", errors="replace")),
        }


@dataclass(frozen=True, slots=True)
class WorkspaceExecutionPolicy:
    """SDK pre-checks for workspace execution backend requests."""

    allowed_capabilities: frozenset[str] | None = None

    def ensure_request_allowed(self, request: WorkspaceExecutionRequest) -> Path:
        """Validate capability, workspace root, and cwd containment."""

        if (
            self.allowed_capabilities is not None
            and request.capability not in self.allowed_capabilities
        ):
            raise WorkspaceExecutionError(
                f"workspace execution capability not allowed: {request.capability}",
            )
        if request.workspace.root is None:
            raise WorkspaceExecutionError(
                "workspace execution requires a workspace root",
            )
        root = _resolve_workspace_path(Path(request.workspace.root))
        cwd = root if request.cwd is None else Path(request.cwd)
        if not cwd.is_absolute():
            cwd = root / cwd
        cwd = _resolve_workspace_path(cwd)
        if not cwd.is_relative_to(root):
            raise WorkspaceExecutionError("cwd escapes workspace root")
        return cwd


class WorkspaceExecutionBackend(Protocol):
    """Boundary for workspace-aware command or sandbox execution."""

    def run(self, request: WorkspaceExecutionRequest) -> WorkspaceExecutionResult:
        """Run one workspace execution request."""

    async def async_run(
        self,
        request: WorkspaceExecutionRequest,
    ) -> WorkspaceExecutionResult:
        """Run one workspace execution request asynchronously."""


class SandboxBackend(WorkspaceExecutionBackend, Protocol):
    """Alias protocol for Docker, E2B, or enterprise sandbox adapters."""


@dataclass(slots=True)
class LocalWorkspaceExecutionBackend:
    """Reference local subprocess backend for trusted development workspaces."""

    policy: WorkspaceExecutionPolicy = field(
        default_factory=WorkspaceExecutionPolicy,
    )
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

        return await asyncio.to_thread(self.run, request)

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

    def _process_env(
        self,
        request: WorkspaceExecutionRequest,
    ) -> dict[str, str]:
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


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    """控制可用 workspace scope 和子 workspace 收窄的策略。"""

    allow_parent_access: bool = False
    allowed_scopes: frozenset[WorkspaceScope] = frozenset({"session", "task"})
    require_explicit_web_workspace: bool = True

    def ensure_scope_allowed(self, scope: WorkspaceScope) -> None:
        if scope not in self.allowed_scopes:
            raise WorkspacePolicyError(f"workspace scope not allowed: {scope}")

    def ensure_child_workspace_allowed(
        self,
        parent: WorkspaceHandle,
        child: WorkspaceHandle,
    ) -> None:
        if _SCOPE_RANK[child.scope] > _SCOPE_RANK[parent.scope]:
            raise WorkspacePolicyError(
                f"cannot broaden workspace from {parent.scope} to {child.scope}",
            )
        self.ensure_scope_allowed(child.scope)
        if child.root is None:
            return
        if parent.root is None:
            raise WorkspacePolicyError(
                "child workspace root requires parent workspace root",
            )
        parent_root = _resolve_policy_path(Path(parent.root))
        child_root = _resolve_policy_path(Path(child.root))
        if not child_root.is_relative_to(parent_root):
            raise WorkspacePolicyError("child workspace root escapes parent workspace root")
        if child_root == parent_root and not self.allow_parent_access:
            raise WorkspacePolicyError(
                "child workspace root must narrow parent workspace root",
            )


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
        base = _resolve_policy_path(Path.cwd() if self.base_dir is None else Path(self.base_dir))
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


def _resolve_policy_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspacePolicyError("workspace path could not be resolved") from exc


def _resolve_workspace_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspaceExecutionError("workspace path could not be resolved") from exc


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _json_safe_value(value, key_hint=str(key))
        for key, value in values.items()
    }


def _json_safe_value(value: object, *, key_hint: str = "") -> object:
    if is_secret_like_key(key_hint):
        return "<redacted>"
    if isinstance(value, str):
        return redact_secret_patterns(value)
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return redact_secret_patterns(repr(value))


__all__ = [
    "LocalWorkspaceExecutionBackend",
    "LocalWorkspaceProvider",
    "SandboxBackend",
    "WORKSPACE_EXECUTION_ISOLATION_REQUIRED_COMPONENTS",
    "WorkspaceExecutionBackend",
    "WorkspaceExecutionError",
    "WorkspaceExecutionIsolationProfile",
    "WorkspaceExecutionPolicy",
    "WorkspaceExecutionRequest",
    "WorkspaceExecutionResult",
    "WorkspaceHandle",
    "WorkspacePolicy",
    "WorkspacePolicyError",
    "WorkspaceProvider",
    "WorkspaceRequest",
    "WorkspaceScope",
]
