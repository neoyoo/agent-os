from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol

from agentos._json_values import FrozenJsonObject
from agentos._redaction import (
    is_secret_like_key,
    redact_command_argv,
    redact_secret_patterns,
)


WorkspaceScope = Literal["process", "agent", "user", "session", "team", "task"]
WORKSPACE_SCOPES: tuple[WorkspaceScope, ...] = (
    "process",
    "agent",
    "user",
    "session",
    "team",
    "task",
)


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    """执行工作空间的稳定引用。"""

    workspace_id: str
    scope: WorkspaceScope
    root: str | None = None
    parent_workspace_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.scope not in WORKSPACE_SCOPES:
            raise ValueError(f"invalid workspace scope: {self.scope}")
        if not isinstance(self.metadata, Mapping) or any(
            type(key) is not str or type(value) is not str
            for key, value in self.metadata.items()
        ):
            raise TypeError("metadata must contain string keys and values")
        object.__setattr__(self, "metadata", FrozenJsonObject(self.metadata.items()))


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
    "SandboxBackend",
    "WORKSPACE_SCOPES",
    "WorkspaceExecutionBackend",
    "WorkspaceExecutionRequest",
    "WorkspaceExecutionResult",
    "WorkspaceHandle",
    "WorkspaceProvider",
    "WorkspaceRequest",
    "WorkspaceScope",
]
