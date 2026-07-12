from __future__ import annotations

from agentos.context.registry import (
    ContextExtensionRegistry,
    ContextExtensionSpec,
)
from agentos.context.xml import XmlElement, XmlTagSpec


class RecordingTokenCounter:
    def __init__(self, count: int) -> None:
        self.count = count
        self.inputs: list[str] = []

    def count_text(self, text: str) -> int:
        self.inputs.append(text)
        return self.count


def extension_spec(
    namespace: str = "com.example.hitl",
    *,
    max_tokens: int = 512,
) -> ContextExtensionSpec:
    return ContextExtensionSpec(
        namespace=namespace,
        owner="HitlRuntime",
        version="1.0",
        tag_schemas={
            ("approval",): XmlTagSpec(
                required_attributes=("status",),
                canonical_order=("status",),
                allow_text=True,
            ),
        },
        max_tokens=max_tokens,
        trim_rank=5,
    )


def extension_registry(
    *specs: ContextExtensionSpec,
) -> ContextExtensionRegistry:
    registry = ContextExtensionRegistry()
    for spec in specs or (extension_spec(),):
        registry.register(spec)
    return registry


def extension_child(
    namespace: str = "com.example.hitl",
    *,
    version: str = "1.0",
    text: str = "Review quote",
) -> XmlElement:
    return XmlElement(
        "extension",
        (("namespace", namespace), ("version", version)),
        children=(
            XmlElement("approval", (("status", "pending"),), text=text),
        ),
    )


def extensions_element(*children: XmlElement) -> XmlElement:
    return XmlElement("extensions", children=children or (extension_child(),))
