from __future__ import annotations

import asyncio

import pytest

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.projection import (
    project_artifact_catalog,
    project_context_mounts,
)
from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.types import ArtifactNotFoundError


class CountingArtifactStore(InMemoryArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0
        self.read_calls = 0
        self.list_calls = 0

    async def get(self, session_id: str, artifact_id: str):  # type: ignore[no-untyped-def]
        self.get_calls += 1
        return await super().get(session_id, artifact_id)

    async def read(self, session_id: str, artifact_id: str) -> bytes:
        self.read_calls += 1
        return await super().read(session_id, artifact_id)

    async def list(  # type: ignore[no-untyped-def]
        self,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ):
        self.list_calls += 1
        return await super().list(session_id, cursor, limit)


class FailingRefreshArtifactStore(CountingArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_reads = False

    async def read(self, session_id: str, artifact_id: str) -> bytes:
        if self.fail_reads:
            raise ArtifactNotFoundError()
        return await super().read(session_id, artifact_id)


class PausedCatalogArtifactStore(CountingArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.pause_next_list = False
        self.catalog_captured = asyncio.Event()
        self.release_catalog = asyncio.Event()

    async def list(  # type: ignore[no-untyped-def]
        self,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ):
        page = await super().list(session_id, cursor, limit)
        if self.pause_next_list:
            self.pause_next_list = False
            self.catalog_captured.set()
            await self.release_catalog.wait()
        return page


def test_provider_projections_only_read_prepared_turn_cache() -> None:
    async def scenario() -> None:
        store = CountingArtifactStore()
        runtime = ArtifactRuntime(session_id="session_1", store=store)
        record = await runtime.upload(
            data=b"image-bytes",
            filename="drawing.png",
            media_type="image/png",
        )
        await runtime.load_attachment(record.id)
        await runtime.prepare_projection_cache()
        calls_after_prepare = (
            store.get_calls,
            store.read_calls,
            store.list_calls,
        )

        assert project_artifact_catalog(runtime) is not None
        assert len(project_context_mounts(runtime)) == 1
        assert project_artifact_catalog(runtime) is not None
        assert len(project_context_mounts(runtime)) == 1
        assert (
            store.get_calls,
            store.read_calls,
            store.list_calls,
        ) == calls_after_prepare

    asyncio.run(scenario())


def test_failed_projection_refresh_preserves_last_complete_cache() -> None:
    async def scenario() -> None:
        store = FailingRefreshArtifactStore()
        runtime = ArtifactRuntime(session_id="session_1", store=store)
        record = await runtime.upload(
            data=b"image-bytes",
            filename="drawing.png",
            media_type="image/png",
        )
        await runtime.load_attachment(record.id)
        await runtime.prepare_projection_cache()
        previous_catalog = project_artifact_catalog(runtime)
        previous_mounts = project_context_mounts(runtime)

        store.fail_reads = True
        with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
            await runtime.prepare_projection_cache()

        assert project_artifact_catalog(runtime) == previous_catalog
        assert project_context_mounts(runtime) == previous_mounts

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["upload", "delete", "delete_session"])
def test_projection_refresh_retries_after_concurrent_catalog_mutation(
    mutation: str,
) -> None:
    async def scenario() -> None:
        store = PausedCatalogArtifactStore()
        runtime = ArtifactRuntime(session_id="session_1", store=store)
        first = await runtime.upload(
            data=b"first",
            filename="first.png",
            media_type="image/png",
        )
        await runtime.upload(
            data=b"second",
            filename="second.png",
            media_type="image/png",
        )
        store.pause_next_list = True
        preparing = asyncio.create_task(runtime.prepare_projection_cache())
        await asyncio.wait_for(store.catalog_captured.wait(), timeout=5)

        if mutation == "upload":
            await runtime.upload(
                data=b"third",
                filename="third.png",
                media_type="image/png",
            )
        elif mutation == "delete":
            await runtime.delete(first.id)
        else:
            await runtime.delete_session()
        store.release_catalog.set()
        await preparing

        authoritative = await store.list("session_1", limit=20)
        assert runtime.projection_catalog() == authoritative

    asyncio.run(scenario())


def test_dirty_projection_keeps_current_attempt_snapshot_until_refresh() -> None:
    async def scenario() -> None:
        store = CountingArtifactStore()
        runtime = ArtifactRuntime(session_id="session_1", store=store)
        first = await runtime.upload(
            data=b"first",
            filename="first.png",
            media_type="image/png",
        )
        await runtime.prepare_projection_cache()

        second = await runtime.upload(
            data=b"second",
            filename="second.png",
            media_type="image/png",
        )

        assert tuple(
            record.id for record in runtime.projection_catalog().items
        ) == (first.id,)
        await runtime.prepare_projection_cache()
        assert {record.id for record in runtime.projection_catalog().items} == {
            first.id,
            second.id,
        }

    asyncio.run(scenario())
