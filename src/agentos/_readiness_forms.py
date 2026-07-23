from __future__ import annotations

from agentos._readiness_channel_forms import CHANNEL_AGENT_FORMS
from agentos._readiness_form_types import (
    AgentFormReadiness as AgentFormReadiness,
    REQUIRED_READINESS_DIMENSIONS as REQUIRED_READINESS_DIMENSIONS,
    ReadinessDimension as ReadinessDimension,
    ReadinessLevel as ReadinessLevel,
)
from agentos._readiness_orchestration_forms import ORCHESTRATION_AGENT_FORMS


_FORMS = {
    **CHANNEL_AGENT_FORMS,
    **ORCHESTRATION_AGENT_FORMS,
}


def list_agent_form_readiness() -> tuple[AgentFormReadiness, ...]:
    """Return all known agent-form readiness records."""

    return tuple(_FORMS.values())


def get_agent_form_readiness(form_id: str) -> AgentFormReadiness:
    """Return one readiness record by stable form id."""

    return _FORMS[form_id]


__all__ = [
    "AgentFormReadiness",
    "REQUIRED_READINESS_DIMENSIONS",
    "ReadinessDimension",
    "ReadinessLevel",
    "get_agent_form_readiness",
    "list_agent_form_readiness",
]
