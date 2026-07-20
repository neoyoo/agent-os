from datetime import UTC, datetime

import pytest

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.runtime import (
    DEFAULT_ARTIFACT_MAX_SIZE_BYTES,
    DEFAULT_ARTIFACT_MEDIA_TYPES,
    ArtifactPolicy,
    ArtifactRuntime,
)
from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactValidationError,
)
from tests.artifacts._async import async_test


def runtime(
    *,
    session_id: str = "session-1",
    policy: ArtifactPolicy | None = None,
) -> ArtifactRuntime:
    return ArtifactRuntime(
        session_id=session_id,
        store=InMemoryArtifactStore(),
        policy=policy,
    )


async def upload(target: ArtifactRuntime, *, media_type: str = "image/png"):
    return await target.upload(
        data=b"image",
        filename="drawing.png",
        media_type=media_type,
    )


def test_default_artifact_policy_is_frozen_and_exact() -> None:
    policy = ArtifactPolicy()

    assert policy.allowed_media_types == frozenset(
        {
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/pdf",
        }
    )
    assert policy.allowed_media_types is DEFAULT_ARTIFACT_MEDIA_TYPES
    assert policy.max_size_bytes == 25 * 1024 * 1024
    assert policy.max_size_bytes == DEFAULT_ARTIFACT_MAX_SIZE_BYTES


@pytest.mark.parametrize(
    "media_type",
    ["image/gif", "image/jpeg", "image/png", "image/webp", "application/pdf"],
)
@async_test
async def test_runtime_uploads_default_supported_media(media_type: str) -> None:
    target = runtime()

    record = await upload(target, media_type=media_type)

    assert record.session_id == "session-1"
    assert record.media_type == media_type
    assert record.size_bytes == len(b"image")
    assert record.created_at.tzinfo is not None
    assert record.created_at.utcoffset() == datetime.now(UTC).utcoffset()
    assert await target.read(record.id) == b"image"


@async_test
async def test_runtime_rejects_unsupported_media_and_oversized_content() -> None:
    target = runtime(policy=ArtifactPolicy(max_size_bytes=4))

    with pytest.raises(
        ArtifactValidationError,
        match="^unsupported artifact media type$",
    ):
        await upload(target, media_type="text/plain")
    with pytest.raises(
        ArtifactValidationError,
        match="^artifact exceeds maximum size$",
    ):
        await upload(target)
    assert (await target.list()).items == ()


@async_test
async def test_runtime_is_bound_to_one_session_and_lists_only_that_session() -> None:
    store = InMemoryArtifactStore()
    first = ArtifactRuntime(session_id="session-1", store=store)
    second = ArtifactRuntime(session_id="session-2", store=store)
    one = await upload(first)
    two = await upload(second)

    assert (await first.list()).items == (one,)
    assert (await second.list()).items == (two,)

    with pytest.raises(AttributeError):
        first.session_id = "session-2"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        first.policy = ArtifactPolicy()  # type: ignore[misc]
    assert first.session_id == "session-1"
    assert (await first.list()).items == (one,)


@async_test
async def test_load_attachment_mounts_tool_result_and_returns_fixed_text() -> None:
    target = runtime()
    record = await upload(target)

    result = await target.load_attachment(record.id)

    assert result == (
        f"附件已挂载：{record.id}。"
        "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
    )
    assert target.active_mounts() == (
        await target.mount_user_upload(record.id),
    )
    assert target.active_mounts()[0].reason == "tool_result"


@async_test
async def test_load_attachment_upgrades_user_mount_to_tool_result_without_duplicate() -> None:
    target = runtime()
    record = await upload(target)

    first = await target.mount_user_upload(record.id)
    second = await target.mount_user_upload(record.id)
    await target.load_attachment(record.id)

    assert first is second
    assert len(target.active_mounts()) == 1
    assert target.active_mounts()[0].reason == "tool_result"


@async_test
async def test_user_mount_does_not_downgrade_existing_tool_result_mount() -> None:
    target = runtime()
    record = await upload(target)
    await target.load_attachment(record.id)

    tool_mount = target.active_mounts()[0]
    user_mount = await target.mount_user_upload(record.id)

    assert user_mount is tool_mount
    assert target.active_mounts() == (tool_mount,)
    assert tool_mount.reason == "tool_result"


@async_test
async def test_mount_user_upload_does_not_create_artifact() -> None:
    target = runtime()
    missing = "art_00000000-0000-4000-8000-000000000001"

    with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
        await target.mount_user_upload(missing)
    assert (await target.list()).items == ()
    assert target.active_mounts() == ()


@async_test
async def test_clear_mounts_does_not_delete_artifact() -> None:
    target = runtime()
    record = await upload(target)
    mount = await target.mount_user_upload(record.id)

    assert target.clear_mounts() == (mount,)
    assert target.clear_mounts() == ()
    assert target.active_mounts() == ()
    assert await target.read(record.id) == b"image"


@async_test
async def test_delete_and_delete_session_remove_content_and_active_mounts() -> None:
    target = runtime()
    first = await upload(target)
    second = await upload(target)
    await target.mount_user_upload(first.id)
    await target.load_attachment(second.id)

    await target.delete(first.id)
    assert tuple(mount.artifact_id for mount in target.active_mounts()) == (second.id,)
    with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
        await target.read(first.id)

    await target.delete_session()
    assert target.active_mounts() == ()
    assert (await target.list()).items == ()


@pytest.mark.parametrize(
    "policy",
    [
        ArtifactPolicy(allowed_media_types=frozenset()),
        ArtifactPolicy(max_size_bytes=1),
    ],
)
def test_runtime_accepts_explicit_policy(policy: ArtifactPolicy) -> None:
    assert runtime(policy=policy).policy is policy
