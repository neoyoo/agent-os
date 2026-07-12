"""Context Protocol v1 的固定 XML 标签 Schema 声明。"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class XmlTagSpec:
    """一个固定 XML path 的属性、文本与子节点契约。"""

    required_attributes: tuple[str, ...] = ()
    optional_attributes: tuple[str, ...] = ()
    canonical_order: tuple[str, ...] = ()
    allow_text: bool = False
    allowed_children: tuple[str, ...] | None = ()


CORE_XML_SCHEMA: Mapping[tuple[str, ...], XmlTagSpec] = MappingProxyType(
    {
        ("context-snapshot",): XmlTagSpec(
            required_attributes=(
                "protocol",
                "version",
                "origin",
                "authority",
                "persistence",
                "visibility",
            ),
            canonical_order=(
                "protocol",
                "version",
                "origin",
                "authority",
                "persistence",
                "visibility",
            ),
            allowed_children=(
                "declared-schema",
                "working-state",
                "active-plan",
                "inherited-state",
                "compressed-history",
                "memory-context",
                "available-skills",
                "artifact-catalog",
                "extensions",
                "truncated",
            ),
        ),
        ("context-snapshot", "declared-schema"): XmlTagSpec(
            required_attributes=("version",),
            canonical_order=("version",),
            allowed_children=("field",),
        ),
        ("context-snapshot", "declared-schema", "field"): XmlTagSpec(
            required_attributes=("name", "type", "purpose"),
            canonical_order=("name", "type", "purpose"),
        ),
        ("context-snapshot", "working-state"): XmlTagSpec(
            required_attributes=("schema-version",),
            canonical_order=("schema-version",),
            allowed_children=("field",),
        ),
        ("context-snapshot", "working-state", "field"): XmlTagSpec(
            required_attributes=("name",),
            canonical_order=("name",),
            allowed_children=("value", "item"),
        ),
        ("context-snapshot", "working-state", "field", "value"): XmlTagSpec(
            optional_attributes=("format", "null"),
            canonical_order=("format", "null"),
            allow_text=True,
        ),
        ("context-snapshot", "working-state", "field", "item"): XmlTagSpec(
            allow_text=True,
        ),
        ("context-snapshot", "active-plan"): XmlTagSpec(
            required_attributes=("status",),
            canonical_order=("status",),
            allowed_children=("goal", "step"),
        ),
        ("context-snapshot", "active-plan", "goal"): XmlTagSpec(
            allow_text=True,
        ),
        ("context-snapshot", "active-plan", "step"): XmlTagSpec(
            required_attributes=("handle", "status"),
            canonical_order=("handle", "status"),
            allow_text=True,
        ),
        ("context-snapshot", "inherited-state"): XmlTagSpec(
            allowed_children=("item",),
        ),
        ("context-snapshot", "inherited-state", "item"): XmlTagSpec(
            required_attributes=("kind",),
            canonical_order=("kind",),
            allow_text=True,
        ),
        ("context-snapshot", "compressed-history"): XmlTagSpec(
            allowed_children=("segment",),
        ),
        ("context-snapshot", "compressed-history", "segment"): XmlTagSpec(
            required_attributes=("handle", "topic", "recallable"),
            canonical_order=("handle", "topic", "recallable"),
            allow_text=True,
        ),
        ("context-snapshot", "memory-context"): XmlTagSpec(
            allowed_children=("memory",),
        ),
        ("context-snapshot", "memory-context", "memory"): XmlTagSpec(
            required_attributes=("handle", "kind", "category", "instructional"),
            canonical_order=("handle", "kind", "category", "instructional"),
            allow_text=True,
        ),
        ("context-snapshot", "available-skills"): XmlTagSpec(
            required_attributes=("truncated",),
            canonical_order=("truncated",),
            allowed_children=("skill",),
        ),
        ("context-snapshot", "available-skills", "skill"): XmlTagSpec(
            required_attributes=("name", "description", "loadable", "trust"),
            canonical_order=("name", "description", "loadable", "trust"),
        ),
        ("context-snapshot", "artifact-catalog"): XmlTagSpec(
            required_attributes=("scope", "truncated"),
            canonical_order=("scope", "truncated"),
            allowed_children=("artifact",),
        ),
        ("context-snapshot", "artifact-catalog", "artifact"): XmlTagSpec(
            required_attributes=("handle", "filename", "media-type", "state"),
            canonical_order=("handle", "filename", "media-type", "state"),
        ),
        ("context-snapshot", "extensions"): XmlTagSpec(
            allowed_children=("extension",),
        ),
        ("context-snapshot", "extensions", "extension"): XmlTagSpec(
            required_attributes=("namespace", "version"),
            canonical_order=("namespace", "version"),
            allowed_children=None,
        ),
        ("context-snapshot", "truncated"): XmlTagSpec(
            required_attributes=("slot", "reason", "remaining"),
            canonical_order=("slot", "reason", "remaining"),
        ),
    },
)
