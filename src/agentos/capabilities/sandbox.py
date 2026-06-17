from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentos.capabilities.tools import RegisteredTool
from agentos.workspace import WorkspaceHandle


class ToolSandboxError(PermissionError):
    """Raised when a tool call violates a sandbox policy."""


@dataclass(frozen=True, slots=True)
class ToolPathSandboxRule:
    """Declares one string argument that must stay inside the workspace root."""

    argument_name: str


class ToolSandboxPolicy(Protocol):
    """Pre-execution policy for external tool calls."""

    def ensure_tool_call_allowed(
        self,
        tool: RegisteredTool,
        arguments: Mapping[str, object],
    ) -> None:
        """Raise when the tool call must not be executed."""


PathSandboxRuleInput = str | ToolPathSandboxRule


@dataclass(frozen=True, slots=True)
class WorkspaceToolSandboxPolicy:
    """Workspace-aware pre-execution gate for external tools."""

    workspace: WorkspaceHandle | None
    path_rules: Mapping[str, Sequence[PathSandboxRuleInput]] = field(
        default_factory=dict,
    )
    allowed_capabilities: frozenset[str] | None = None
    tool_capabilities: Mapping[str, str | Sequence[str]] = field(default_factory=dict)

    def ensure_tool_call_allowed(
        self,
        tool: RegisteredTool,
        arguments: Mapping[str, object],
    ) -> None:
        """Validate declared capabilities and path-bearing arguments."""

        self._ensure_capabilities_allowed(tool)
        for rule in self._rules_for(tool.name):
            normalized = self._ensure_path_argument_inside_workspace(
                tool,
                arguments,
                rule,
            )
            if normalized is not None and isinstance(arguments, MutableMapping):
                arguments[rule.argument_name] = normalized

    def _ensure_capabilities_allowed(self, tool: RegisteredTool) -> None:
        if self.allowed_capabilities is None:
            return
        capabilities = self._capabilities_for(tool)
        if not capabilities:
            raise ToolSandboxError(
                f"tool capability is required when allowlist is set: {tool.name}",
            )
        for capability in capabilities:
            if capability not in self.allowed_capabilities:
                raise ToolSandboxError(
                    f"tool capability not allowed: {capability}",
                )

    def _ensure_path_argument_inside_workspace(
        self,
        tool: RegisteredTool,
        arguments: Mapping[str, object],
        rule: ToolPathSandboxRule,
    ) -> str | None:
        raw_path = arguments.get(rule.argument_name)
        if raw_path is None:
            return None
        if not isinstance(raw_path, str) or raw_path == "":
            raise ToolSandboxError(
                f"tool {tool.name} path argument must be a non-empty string: "
                f"{rule.argument_name}",
            )
        if self.workspace is None or self.workspace.root is None:
            raise ToolSandboxError(
                f"tool {tool.name} requires a workspace root for sandboxed "
                f"path argument: {rule.argument_name}",
            )

        root = self._resolve_path(Path(self.workspace.root))
        target = Path(raw_path)
        if not target.is_absolute():
            target = root / target
        target = self._resolve_path(target)
        if not target.is_relative_to(root):
            raise ToolSandboxError(
                f"tool {tool.name} path argument escapes workspace root: "
                f"{rule.argument_name}",
            )
        return str(target)

    def _rules_for(self, tool_name: str) -> tuple[ToolPathSandboxRule, ...]:
        return tuple(
            rule if isinstance(rule, ToolPathSandboxRule) else ToolPathSandboxRule(rule)
            for rule in self.path_rules.get(tool_name, ())
        )

    def _capabilities_for(self, tool: RegisteredTool) -> tuple[str, ...]:
        raw_capabilities: list[object] = []
        if tool.name in self.tool_capabilities:
            raw_capabilities.append(self.tool_capabilities[tool.name])
        raw_capabilities.append(tool.metadata.get("capability"))
        raw_capabilities.append(tool.metadata.get("capabilities"))

        capabilities: list[str] = []
        for raw in raw_capabilities:
            if raw is None:
                continue
            if isinstance(raw, str):
                capabilities.append(raw)
                continue
            if isinstance(raw, Iterable):
                capabilities.extend(item for item in raw if isinstance(item, str))
        return tuple(dict.fromkeys(capabilities))

    def _resolve_path(self, path: Path) -> Path:
        try:
            return path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as error:
            raise ToolSandboxError("tool path could not be resolved") from error
