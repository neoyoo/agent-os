from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentos import AgentBuilder
from agentos.durable import profile as profile_module
from agentos.capabilities.skill_activation import SkillActivationStoreClosedError
from agentos.durable import DurableRuntimeProfile
from agentos.memory.sqlite_errors import SQLiteMemoryStoreClosedError
from agentos.planning.sqlite_errors import SQLitePlanStoreClosedError
from agentos.providers import FakeProvider
from agentos.runtime.errors import AgentBusyError, DurableStoreClosedError
from agentos.security import FernetPayloadProtector


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
_PAYLOAD_PROTECTOR = FernetPayloadProtector(
    FernetPayloadProtector.generate_key(),
)


def _profile(tmp_path, builder: AgentBuilder) -> DurableRuntimeProfile:
    return DurableRuntimeProfile(
        agent_builder=builder,
        database_path=tmp_path / "state.db",
        artifact_root=tmp_path / "artifacts",
        clock=lambda: NOW,
        payload_protector=_PAYLOAD_PROTECTOR,
    )


def test_profile_extension_store_access_is_closed_with_profile(tmp_path) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider([])))
        await profile.open()
        plan_store = profile.plan_store
        memory_store = profile.memory_store
        activation_store = profile.skill_activation_store

        await profile.close()
        await profile.close()

        with pytest.raises(
            DurableStoreClosedError,
            match="^durable profile is not open$",
        ):
            _ = profile.plan_store
        with pytest.raises(SQLitePlanStoreClosedError):
            await plan_store.list_plans()
        with pytest.raises(SQLiteMemoryStoreClosedError):
            await memory_store.get("mem_1")
        with pytest.raises(SkillActivationStoreClosedError):
            await activation_store.list("session_1")

    asyncio.run(scenario())


def test_profile_close_invalidates_old_agent_artifact_access(tmp_path) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider([])))
        await profile.open()
        agent = await profile.build_agent("session_1")
        record = await agent.artifacts.upload(
            data=b"drawing",
            filename="drawing.png",
            media_type="image/png",
        )

        await profile.close()
        await profile.close()

        async def upload() -> None:
            await agent.artifacts.upload(
                data=b"new",
                filename="new.png",
                media_type="image/png",
            )

        async def read() -> None:
            await agent.artifacts.read(record.id)

        async def list_artifacts() -> None:
            await agent.artifacts.list()

        for operation in (upload, read, list_artifacts):
            with pytest.raises(
                DurableStoreClosedError,
                match="^durable artifact store is closed$",
            ) as error:
                await operation()
            assert str(tmp_path) not in str(error.value)

    asyncio.run(scenario())


def test_profile_close_rejects_active_stream_without_closing_store(
    tmp_path,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        await profile.open()
        agent = await profile.build_agent("session_1")
        stream = await agent.run("start", stream=True)

        with pytest.raises(AgentBusyError, match="active execution"):
            await profile.close()

        rebuilt = await profile.build_agent("session_1")
        assert rebuilt.query_loop is agent.query_loop
        await stream.aclose()
        await profile.close()
        await profile.close()

    asyncio.run(scenario())


def test_profile_close_observes_execution_reservation_during_prepare(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        await profile.open()
        agent = await profile.build_agent("session_1")
        prepare_entered = asyncio.Event()
        allow_prepare = asyncio.Event()
        original_create = profile._store.create

        async def blocked_create(state):  # type: ignore[no-untyped-def]
            prepare_entered.set()
            await allow_prepare.wait()
            return await original_create(state)

        monkeypatch.setattr(profile._store, "create", blocked_create)
        starting = asyncio.create_task(agent.run("start", stream=True))
        await asyncio.wait_for(prepare_entered.wait(), timeout=5)
        try:
            with pytest.raises(AgentBusyError, match="active execution"):
                await profile.close()
        finally:
            allow_prepare.set()

        stream = await starting
        await stream.aclose()
        await profile.close()

    asyncio.run(scenario())


def test_profile_close_reservation_blocks_new_run_until_store_is_closed(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        await profile.open()
        agent = await profile.build_agent("session_1")
        close_entered = asyncio.Event()
        allow_close = asyncio.Event()
        original_close = profile._skill_activation_store.close

        async def blocked_close() -> None:
            close_entered.set()
            await allow_close.wait()
            await original_close()

        monkeypatch.setattr(
            profile._skill_activation_store,
            "close",
            blocked_close,
        )
        closing = asyncio.create_task(profile.close())
        await asyncio.wait_for(close_entered.wait(), timeout=5)
        try:
            with pytest.raises(AgentBusyError, match="active execution"):
                await agent.run("must not start", stream=True)
        finally:
            allow_close.set()
            await closing

    asyncio.run(scenario())


def test_profile_close_rolls_back_other_session_reservations_when_busy(
    tmp_path,
) -> None:
    async def scenario() -> None:
        profile = _profile(
            tmp_path,
            AgentBuilder().provider(FakeProvider(["one", "two"])),
        )
        await profile.open()
        idle_agent = await profile.build_agent("session_idle")
        busy_agent = await profile.build_agent("session_busy")
        busy_stream = await busy_agent.run("busy", stream=True)

        with pytest.raises(AgentBusyError, match="active execution"):
            await profile.close()

        idle_stream = await idle_agent.run("still available", stream=True)
        await idle_stream.aclose()
        await busy_stream.aclose()
        await profile.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failed_store",
    ("durable", "plan", "memory", "skill", "artifact"),
)
def test_profile_open_failure_closes_prior_stores_in_reverse_order(
    tmp_path,
    monkeypatch,
    failed_store: str,
) -> None:
    async def scenario() -> None:
        opened: list[str] = []
        closed: list[str] = []

        class Store:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                closed.append(self.name)

        def store_type(name: str):  # type: ignore[no-untyped-def]
            class StoreType:
                @classmethod
                async def open(cls, *args, **kwargs):  # type: ignore[no-untyped-def]
                    if name == failed_store:
                        raise RuntimeError(f"{name} open failed")
                    opened.append(name)
                    return Store(name)

            return StoreType

        for attribute, name in (
            ("SQLiteDurableStore", "durable"),
            ("SQLitePlanStore", "plan"),
            ("SQLiteMemoryStore", "memory"),
            ("SQLiteSkillActivationStore", "skill"),
            ("SqliteFilesystemArtifactStore", "artifact"),
        ):
            monkeypatch.setattr(profile_module, attribute, store_type(name))

        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider([])))
        with pytest.raises(RuntimeError, match=f"^{failed_store} open failed$"):
            await profile.open()

        assert profile.is_open is False
        assert closed == list(reversed(opened))

    asyncio.run(scenario())


def test_profile_partial_close_failure_is_retryable_and_blocks_old_agent(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        await profile.open()
        agent = await profile.build_agent("session_1")
        artifact_store = profile._artifact_store
        assert artifact_store is not None
        original_close = artifact_store.close
        close_calls = 0

        async def fail_once() -> None:
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("artifact close failed")
            await original_close()

        monkeypatch.setattr(artifact_store, "close", fail_once)

        with pytest.raises(RuntimeError, match="^artifact close failed$"):
            await profile.close()
        assert profile.is_open is False
        with pytest.raises(AgentBusyError, match="active execution"):
            await agent.run("must remain blocked", stream=True)

        await profile.close()
        await profile.close()
        assert close_calls == 2

    asyncio.run(scenario())


def test_profile_close_cancellation_finishes_cleanup_and_blocks_old_agent(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        await profile.open()
        agent = await profile.build_agent("session_1")
        artifact_store = profile._artifact_store
        assert artifact_store is not None
        original_close = artifact_store.close
        close_entered = asyncio.Event()
        allow_close = asyncio.Event()

        async def blocked_close() -> None:
            close_entered.set()
            await allow_close.wait()
            await original_close()

        monkeypatch.setattr(artifact_store, "close", blocked_close)
        closing = asyncio.create_task(profile.close())
        await asyncio.wait_for(close_entered.wait(), timeout=5)
        closing.cancel("cancel profile close")
        allow_close.set()

        with pytest.raises(asyncio.CancelledError) as caught:
            await closing

        assert caught.value.args == ("cancel profile close",)
        assert profile.is_open is False
        with pytest.raises(AgentBusyError, match="active execution"):
            await agent.run("must remain blocked", stream=True)
        await profile.close()

    asyncio.run(scenario())
