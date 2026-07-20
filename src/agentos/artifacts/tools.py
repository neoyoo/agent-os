import json

from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.types import ArtifactToolItem, ArtifactToolPage
from agentos.providers.tool_specs import ProviderFunctionSpec, ProviderToolSpec


_ARTIFACT_ID_PATTERN = (
    r"^art_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ARTIFACT_TOOL_SPECS = (
    ProviderToolSpec(
        function=ProviderFunctionSpec(
            name="list_attachments",
            description="按最新优先分页列出当前 Session 可用的附件元数据。",
            parameters={
                "type": "object",
                "properties": {
                    "cursor": {
                        "anyOf": ({"type": "string"}, {"type": "null"}),
                        "default": None,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
                "additionalProperties": False,
            },
        )
    ),
    ProviderToolSpec(
        function=ProviderFunctionSpec(
            name="load_attachment",
            description=(
                "把当前 Session 中的附件挂载到当前 Turn 的下一次模型请求。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "pattern": _ARTIFACT_ID_PATTERN,
                    },
                },
                "required": ("handle",),
                "additionalProperties": False,
            },
        )
    ),
)


def artifact_tool_specs() -> tuple[ProviderToolSpec, ...]:
    """返回 Artifact Tool schema 的唯一权威定义。"""

    return _ARTIFACT_TOOL_SPECS


async def list_attachments(
    runtime: ArtifactRuntime,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> str:
    """返回当前 Session 的 canonical 模型安全 Artifact 分页。"""

    page = await runtime.list(cursor=cursor, limit=limit)
    mounted_ids = frozenset(mount.artifact_id for mount in runtime.active_mounts())
    tool_page = ArtifactToolPage(
        items=tuple(
            ArtifactToolItem(
                handle=record.id,
                filename=record.filename,
                media_type=record.media_type,
                state="mounted" if record.id in mounted_ids else "available",
            )
            for record in page.items
        ),
        next_cursor=page.next_cursor,
    )
    return json.dumps(
        {
            "items": [
                {
                    "handle": item.handle,
                    "filename": item.filename,
                    "media_type": item.media_type,
                    "state": item.state,
                }
                for item in tool_page.items
            ],
            "next_cursor": tool_page.next_cursor,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


async def load_attachment(runtime: ArtifactRuntime, *, handle: str) -> str:
    """执行 ArtifactRuntime 的有界附件挂载操作。"""

    return await runtime.load_attachment(handle)
