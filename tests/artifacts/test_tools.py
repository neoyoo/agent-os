from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime

import pytest

import agentos.artifacts as public_artifacts
from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.tools import (
    artifact_tool_specs,
    list_attachments,
    load_attachment,
)
from agentos.artifacts.types import (
    ArtifactToolItem,
    ArtifactToolPage,
    ArtifactValidationError,
)
from agentos.context_protocol import context_protocol_tool_specs
from agentos.providers import provider_tool_spec_to_dict


def runtime() -> ArtifactRuntime:
    ids = iter(
        f"art_00000000-0000-4000-8000-{index:012x}"
        for index in range(1, 10)
    )
    return ArtifactRuntime(
        session_id="session-secret",
        store=InMemoryArtifactStore(
            clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
            id_factory=lambda: next(ids),
        ),
    )


def upload(target: ArtifactRuntime, filename: str = "drawing.png"):
    return target.upload(
        data=b"private-image",
        filename=filename,
        media_type="image/png",
    )


def test_artifact_tool_page_is_frozen_and_model_safe() -> None:
    item = ArtifactToolItem(
        handle="art_00000000-0000-4000-8000-000000000001",
        filename="drawing.png",
        media_type="image/png",
        state="available",
    )
    page = ArtifactToolPage(items=[item], next_cursor="next")

    assert page.items == (item,)
    assert asdict(page) == {
        "items": (
            {
                "handle": item.handle,
                "filename": "drawing.png",
                "media_type": "image/png",
                "state": "available",
            },
        ),
        "next_cursor": "next",
    }
    for forbidden in (
        "session_id",
        "created_at",
        "size_bytes",
        "bytes",
        "path",
    ):
        assert forbidden not in repr(page)
    with pytest.raises(FrozenInstanceError):
        item.state = "mounted"  # type: ignore[misc]


def test_list_attachments_returns_safe_page_and_mounted_state() -> None:
    target = runtime()
    first = upload(target, "first.png")
    second = upload(target, "second.png")
    target.load_attachment(second.id)

    page = list_attachments(target, limit=1)

    assert page.items == (
        ArtifactToolItem(
            handle=second.id,
            filename="second.png",
            media_type="image/png",
            state="mounted",
        ),
    )
    assert page.next_cursor is not None
    next_page = list_attachments(target, cursor=page.next_cursor, limit=1)
    assert next_page.items[0].handle == first.id
    assert next_page.items[0].state == "available"
    assert "session-secret" not in repr(page)
    assert "private-image" not in repr(page)


def test_list_attachments_reuses_store_limit_validation() -> None:
    with pytest.raises(ArtifactValidationError, match="artifact limit is invalid"):
        list_attachments(runtime(), limit=101)


def test_load_attachment_handler_uses_artifact_handle_and_fixed_result() -> None:
    target = runtime()
    record = upload(target)

    assert load_attachment(target, handle=record.id) == (
        f"附件已挂载：{record.id}。"
        "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
    )
    with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
        load_attachment(target, handle="att:legacy")


def test_artifact_tool_specs_freeze_list_and_load_schema() -> None:
    specs = tuple(provider_tool_spec_to_dict(spec) for spec in artifact_tool_specs())

    assert [spec["function"]["name"] for spec in specs] == [  # type: ignore[index]
        "list_attachments",
        "load_attachment",
    ]
    list_parameters = specs[0]["function"]["parameters"]  # type: ignore[index]
    assert list_parameters["properties"]["limit"] == {  # type: ignore[index]
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 20,
    }
    load_parameters = specs[1]["function"]["parameters"]  # type: ignore[index]
    handle_schema = load_parameters["properties"]["handle"]  # type: ignore[index]
    assert handle_schema["pattern"].startswith("^art_")
    assert load_parameters["required"] == ["handle"]  # type: ignore[index]
    assert "att:" not in repr(specs)


def test_artifact_tools_remain_internal_until_phase4() -> None:
    assert not hasattr(public_artifacts, "artifact_tool_specs")
    global_names = {
        spec.function.name for spec in context_protocol_tool_specs()
    }
    assert "list_attachments" not in global_names
    assert "delete_attachment" not in {
        spec.function.name for spec in artifact_tool_specs()
    }
