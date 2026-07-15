from datetime import UTC, datetime

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.projection import project_artifact_catalog
from agentos.artifacts.runtime import ArtifactRuntime


def runtime_with_stable_ids() -> ArtifactRuntime:
    ids = iter(
        f"art_00000000-0000-4000-8000-{index:012x}"
        for index in range(1, 30)
    )
    store = InMemoryArtifactStore(
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
        id_factory=lambda: next(ids),
    )
    return ArtifactRuntime(session_id="session-1", store=store)


def upload(target: ArtifactRuntime, filename: str | None = "drawing.png"):
    return target.upload(
        data=b"image",
        filename=filename,
        media_type="image/png",
    )


def test_empty_artifact_catalog_does_not_emit_empty_slot() -> None:
    assert project_artifact_catalog(runtime_with_stable_ids()) is None


def test_catalog_projects_latest_twenty_in_stable_order_and_marks_more() -> None:
    target = runtime_with_stable_ids()
    records = [upload(target, f"drawing-{index}.png") for index in range(25)]
    target.mount_user_upload(records[-1].id)

    projection = project_artifact_catalog(target)

    assert projection is not None
    assert projection.slot == "artifact-catalog"
    assert projection.owner == "ArtifactRuntime"
    full = projection.variants[0].element
    assert full.attributes == (("scope", "session"), ("truncated", "true"))
    assert tuple(dict(child.attributes)["handle"] for child in full.children) == tuple(
        record.id for record in reversed(records[-20:])
    )
    assert dict(full.children[0].attributes)["state"] == "mounted"
    assert all(
        dict(child.attributes)["state"] == "available"
        for child in full.children[1:]
    )


def test_catalog_contains_only_context_protocol_metadata() -> None:
    target = runtime_with_stable_ids()
    record = upload(target, None)

    projection = project_artifact_catalog(target)

    assert projection is not None
    attributes = dict(projection.variants[0].element.children[0].attributes)
    assert attributes == {
        "handle": record.id,
        "filename": "",
        "media-type": "image/png",
        "state": "available",
    }
    serialized = repr(projection)
    for forbidden in ("session-1", "size_bytes", "created_at", "data=b"):
        assert forbidden not in serialized


def test_catalog_budget_variants_remove_whole_artifacts_only() -> None:
    target = runtime_with_stable_ids()
    for index in range(4):
        upload(target, f"drawing-{index}.png")

    projection = project_artifact_catalog(target)

    assert projection is not None
    assert len(projection.variants) == 5
    assert tuple(variant.omitted_count for variant in projection.variants) == (
        0,
        1,
        2,
        3,
        4,
    )
    assert tuple(
        len(variant.element.children) for variant in projection.variants
    ) == (4, 3, 2, 1, 0)
    assert dict(projection.variants[0].element.attributes)["truncated"] == "false"
    assert all(
        dict(variant.element.attributes)["truncated"] == "true"
        for variant in projection.variants[1:]
    )
