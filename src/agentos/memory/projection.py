from collections.abc import Iterable

from agentos.context.models import ContextSlotProjection, ProjectionVariant
from agentos.context.xml import XmlElement
from agentos.memory.records import MemoryRecord


def project_memory_context(
    records: Iterable[MemoryRecord],
) -> tuple[ContextSlotProjection, ...]:
    """把已选择的完整 MemoryRecord 投影为 memory-context。"""

    selected = tuple(records)
    if not selected:
        return ()
    variants = tuple(
        ProjectionVariant(
            element=XmlElement(
                "memory-context",
                children=tuple(_memory_element(record) for record in selected[:count]),
            ),
            omitted_count=len(selected) - count,
        )
        for count in range(len(selected), 0, -1)
    )
    return (
        ContextSlotProjection(
            slot="memory-context",
            owner="MemoryRuntime",
            variants=variants,
        ),
    )


def _memory_element(record: MemoryRecord) -> XmlElement:
    return XmlElement(
        "memory",
        (
            ("handle", record.handle),
            ("kind", record.kind),
            ("category", record.category),
            ("instructional", "false"),
        ),
        text=record.content,
    )
