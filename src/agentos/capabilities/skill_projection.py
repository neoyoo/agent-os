from __future__ import annotations

from collections.abc import Iterable

from agentos.capabilities.skill_types import SkillDescriptor
from agentos.context.models import ContextSlotProjection, ProjectionVariant
from agentos.context.xml import XmlElement


def project_available_skills(
    descriptors: Iterable[SkillDescriptor],
) -> tuple[ContextSlotProjection, ...]:
    """把安全 Skill Metadata 投影为 available-skills。"""

    descriptors = tuple(descriptors)
    if not descriptors:
        return ()
    full = XmlElement(
        "available-skills",
        (("truncated", "false"),),
        children=tuple(_skill_element(item) for item in descriptors),
    )
    compact = XmlElement("available-skills", (("truncated", "true"),))
    return (
        ContextSlotProjection(
            slot="available-skills",
            owner="SkillRuntime",
            variants=(
                ProjectionVariant(full),
                ProjectionVariant(compact, omitted_count=len(descriptors)),
            ),
        ),
    )


def _skill_element(descriptor: SkillDescriptor) -> XmlElement:
    metadata = descriptor.metadata
    return XmlElement(
        "skill",
        (
            ("name", metadata.name),
            ("description", metadata.description),
            ("loadable", str(metadata.loadable).lower()),
            ("trust", metadata.trust),
        ),
    )
