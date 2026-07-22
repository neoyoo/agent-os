from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentos.workspace.models import (
    WorkspaceExecutionRequest,
    WorkspaceHandle,
    WorkspaceScope,
)


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
        return {**metadata, "status": "ok" if ok else "failed", "ok": ok}

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


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


__all__ = [
    "WORKSPACE_EXECUTION_ISOLATION_REQUIRED_COMPONENTS",
    "WorkspaceExecutionError",
    "WorkspaceExecutionIsolationProfile",
    "WorkspaceExecutionPolicy",
    "WorkspacePolicy",
    "WorkspacePolicyError",
]
