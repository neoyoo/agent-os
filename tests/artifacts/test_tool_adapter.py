from datetime import UTC, datetime
import json

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.tool_adapter import ArtifactToolAdapter
from agentos.artifacts.tools import artifact_tool_specs
from agentos.capabilities import ToolConcurrencyPolicy
from tests.artifacts._async import async_test


def runtime() -> ArtifactRuntime:
    return ArtifactRuntime(
        session_id="session-secret",
        store=InMemoryArtifactStore(
            clock=lambda: datetime(2026, 7, 16, tzinfo=UTC),
            id_factory=lambda: "art_00000000-0000-4000-8000-000000000001",
        ),
    )


def test_adapter_reuses_artifact_schemas_and_freezes_exclusive_policy() -> None:
    registered = ArtifactToolAdapter(runtime()).registered_tools()

    assert tuple(tool.name for tool in registered) == (
        "list_attachments",
        "load_attachment",
    )
    assert tuple(tool.provider_spec() for tool in registered) == artifact_tool_specs()
    assert all(
        tool.concurrency_policy is ToolConcurrencyPolicy.EXCLUSIVE
        for tool in registered
    )
    assert all(tool.kind == "external" for tool in registered)


@async_test
async def test_adapter_handlers_are_bound_to_one_artifact_runtime() -> None:
    target = runtime()
    record = await target.upload(
        data=b"private-image",
        filename="drawing.png",
        media_type="image/png",
    )
    tools = {
        tool.name: tool for tool in ArtifactToolAdapter(target).registered_tools()
    }

    listed = await tools["list_attachments"].handler({})
    assert isinstance(listed, str)
    assert json.loads(listed) == {
        "items": [
            {
                "filename": "drawing.png",
                "handle": record.id,
                "media_type": "image/png",
                "state": "available",
            }
        ],
        "next_cursor": None,
    }
    assert "private-image" not in listed
    assert "session-secret" not in listed

    loaded = await tools["load_attachment"].handler({"handle": record.id})
    assert loaded == (
        f"附件已挂载：{record.id}。"
        "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
    )
    assert target.active_mounts()[0].artifact_id == record.id
