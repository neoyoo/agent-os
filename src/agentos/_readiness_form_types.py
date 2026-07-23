from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


ReadinessLevel = Literal[
    "direct",
    "primitives-ready",
    "future-extension",
    "not-applicable",
]

REQUIRED_READINESS_DIMENSIONS: tuple[str, ...] = (
    "session_state",
    "concurrency",
    "auth",
    "rate_limit",
    "timeout",
    "retry",
    "observability",
    "workspace",
    "protocol",
    "persistence",
    "schema_migration",
)

WORKSPACE_BACKEND_EVIDENCE: tuple[str, ...] = (
    "WorkspaceExecutionBackend",
    "LocalWorkspaceExecutionBackend",
    "SandboxBackend",
    "WorkspaceExecutionRequest",
    "WorkspaceExecutionResult",
    "WorkspaceExecutionPolicy",
    "JSON-safe execution evidence",
)


@dataclass(frozen=True, slots=True)
class ReadinessDimension:
    """One production-readiness dimension for an agent form."""

    name: str
    level: ReadinessLevel
    evidence: tuple[str, ...]
    gap: str = ""


@dataclass(frozen=True, slots=True)
class AgentFormReadiness:
    """Production-readiness record for one supported agent shape."""

    form_id: str
    name: str
    overall_level: ReadinessLevel
    summary: str
    dimensions: Mapping[str, ReadinessDimension]
    recommended_profile: str
    required_app_glue: tuple[str, ...] = ()


def _dimensions(
    overrides: Mapping[str, tuple[ReadinessLevel, tuple[str, ...], str]],
    *,
    default_level: ReadinessLevel = "direct",
    default_evidence: tuple[str, ...] = ("AgentBuilder", "QueryLoop tests"),
    default_gap: str = "",
) -> dict[str, ReadinessDimension]:
    dimensions: dict[str, ReadinessDimension] = {}
    for name in REQUIRED_READINESS_DIMENSIONS:
        level, evidence, gap = overrides.get(
            name,
            (default_level, default_evidence, default_gap),
        )
        dimensions[name] = ReadinessDimension(
            name=name,
            level=level,
            evidence=evidence,
            gap=gap,
        )
    return dimensions


__all__ = [
    "AgentFormReadiness",
    "REQUIRED_READINESS_DIMENSIONS",
    "ReadinessDimension",
    "ReadinessLevel",
]
