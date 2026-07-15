from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.types import ArtifactRecord
from agentos.context.models import ContextSlotProjection, ProjectionVariant
from agentos.context.xml import XmlElement


_CATALOG_LIMIT = 20


def project_artifact_catalog(
    runtime: ArtifactRuntime,
) -> ContextSlotProjection | None:
    """从 ArtifactStore 真值生成有界 Session Catalog 投影。"""

    page = runtime.list(limit=_CATALOG_LIMIT)
    if not page.items:
        return None
    mounted_ids = frozenset(mount.artifact_id for mount in runtime.active_mounts())
    children = tuple(
        _artifact_element(record, mounted=record.id in mounted_ids)
        for record in page.items
    )
    variants = tuple(
        ProjectionVariant(
            element=_catalog_element(
                children[:keep_count],
                truncated=page.next_cursor is not None or keep_count < len(children),
            ),
            omitted_count=len(children) - keep_count,
        )
        for keep_count in range(len(children), -1, -1)
    )
    return ContextSlotProjection(
        slot="artifact-catalog",
        owner="ArtifactRuntime",
        variants=variants,
    )


def _catalog_element(
    children: tuple[XmlElement, ...],
    *,
    truncated: bool,
) -> XmlElement:
    return XmlElement(
        tag="artifact-catalog",
        attributes=(
            ("scope", "session"),
            ("truncated", "true" if truncated else "false"),
        ),
        children=children,
    )


def _artifact_element(record: ArtifactRecord, *, mounted: bool) -> XmlElement:
    return XmlElement(
        tag="artifact",
        attributes=(
            ("handle", record.id),
            ("filename", record.filename or ""),
            ("media-type", record.media_type),
            ("state", "mounted" if mounted else "available"),
        ),
    )
