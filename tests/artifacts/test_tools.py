from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime
import json

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
from tests.artifacts._async import async_test


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


async def upload(target: ArtifactRuntime, filename: str = "drawing.png"):
    return await target.upload(
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


@async_test
async def test_list_attachments_returns_canonical_json_and_mounted_state() -> None:
    target = runtime()
    first = await upload(target, "first.png")
    second = await upload(target, "图纸.png")
    await target.load_attachment(second.id)

    result = await list_attachments(target, limit=1)

    assert result.startswith(
        '{"items":[{"filename":"图纸.png",'
        f'"handle":"{second.id}",'
        '"media_type":"image/png","state":"mounted"}],'
        '"next_cursor":"'
    )
    assert "\\u56fe" not in result

    page = json.loads(result)
    next_cursor = page["next_cursor"]
    assert isinstance(next_cursor, str)
    next_result = await list_attachments(target, cursor=next_cursor, limit=1)
    assert next_result == (
        '{"items":[{"filename":"first.png",'
        f'"handle":"{first.id}",'
        '"media_type":"image/png","state":"available"}],'
        '"next_cursor":null}'
    )
    for forbidden in (
        "session-secret",
        "private-image",
        "created_at",
        "size_bytes",
        "bytes",
        "path",
        "base64",
        "provider_file_id",
    ):
        assert forbidden not in result


@async_test
async def test_list_attachments_empty_page_has_frozen_shape() -> None:
    assert await list_attachments(runtime()) == '{"items":[],"next_cursor":null}'


@async_test
async def test_list_attachments_reuses_store_limit_validation() -> None:
    with pytest.raises(ArtifactValidationError, match="artifact limit is invalid"):
        await list_attachments(runtime(), limit=101)


@async_test
async def test_load_attachment_handler_uses_artifact_handle_and_fixed_result() -> None:
    target = runtime()
    record = await upload(target)

    assert await load_attachment(target, handle=record.id) == (
        f"附件已挂载：{record.id}。"
        "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
    )
    with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
        await load_attachment(target, handle="att:legacy")


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


def test_artifact_tools_have_no_legacy_or_unapproved_operations() -> None:
    assert not hasattr(public_artifacts, "artifact_tool_specs")
    global_names = {
        spec.function.name for spec in context_protocol_tool_specs()
    }
    assert "list_attachments" not in global_names
    assert "delete_attachment" not in {
        spec.function.name for spec in artifact_tool_specs()
    }
