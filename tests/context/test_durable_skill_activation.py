from __future__ import annotations

import asyncio
import sqlite3
import traceback

import pytest

from agentos.capabilities.skill_activation import (
    SQLiteSkillActivationStore,
    SkillActivationCorruptedError,
    SkillActivationRecord,
    SkillActivationStoreClosedError,
)
from agentos.capabilities.skills import (
    SkillContentSource,
    SkillDescriptor,
    SkillLoadResult,
    SkillMetadata,
    SkillRegistry,
    SkillResourceLoadResult,
    SkillResourceRef,
    SkillRuntime,
    SkillTrustDecision,
    SkillVerificationSubject,
)


class MutableSkillSource(SkillContentSource):
    def __init__(self) -> None:
        self.revision = "1"
        self.content_suffix = ""
        self.descriptor = SkillDescriptor(
            SkillMetadata("review", "Review code.", True, "trusted"),
            "Review code.",
        )

    async def list_skills(self) -> list[SkillDescriptor]:
        return [self.descriptor]

    def current_subject(self, name: str) -> SkillVerificationSubject | None:
        if name != "review":
            return None
        return self._subject()

    async def load_skill(self, name: str) -> SkillLoadResult:
        return SkillLoadResult(
            name=name,
            content=self._content(),
            metadata=self.descriptor.metadata,
            subject=self._subject(),
        )

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        return ()

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        raise KeyError(path)

    def _content(self) -> str:
        return f"# Review\nrevision={self.revision}{self.content_suffix}"

    def _subject(self) -> SkillVerificationSubject:
        return SkillVerificationSubject.from_content(
            source_id="mutable",
            skill_name="review",
            source_revision=self.revision,
            content=self._content(),
        )


class MutableTrustPolicy:
    def __init__(self) -> None:
        self.policy_id = "policy-v1"

    def verify(self, metadata, subject):  # type: ignore[no-untyped-def]
        return SkillTrustDecision(True, self.policy_id, subject)


async def _runtime(
    source: MutableSkillSource,
    policy: MutableTrustPolicy,
    store: SQLiteSkillActivationStore,
) -> SkillRuntime:
    registry = await SkillRegistry.aload(source)
    return SkillRuntime(registry, policy, activation_store=store)


def test_trusted_activation_is_reloaded_and_reverified_after_restart(tmp_path) -> None:
    path = tmp_path / "state.db"
    source = MutableSkillSource()
    policy = MutableTrustPolicy()

    async def scenario() -> None:
        first_store = SQLiteSkillActivationStore(path)
        first = await _runtime(source, policy, first_store)
        await first.load("session-a", "review")
        first_store.close()

        second_store = SQLiteSkillActivationStore(path)
        second = await _runtime(source, policy, second_store)
        assert await second.restore("session-a") == ("review",)
        assert second.items("session-a")[0].text.startswith("# Review")
        second_store.close()

    asyncio.run(scenario())

    connection = sqlite3.connect(path)
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(agentos_skill_activations)",
        ).fetchall()
    }
    connection.close()
    assert "content" not in columns


def test_restore_removes_activation_when_source_revision_changes(tmp_path) -> None:
    path = tmp_path / "state.db"
    source = MutableSkillSource()
    policy = MutableTrustPolicy()

    async def scenario() -> None:
        store = SQLiteSkillActivationStore(path)
        runtime = await _runtime(source, policy, store)
        await runtime.load("session-a", "review")
        store.close()

        source.revision = "2"
        reopened = SQLiteSkillActivationStore(path)
        restored = await _runtime(source, policy, reopened)
        assert await restored.restore("session-a") == ()
        assert restored.items("session-a") == ()
        assert reopened.list("session-a") == ()
        reopened.close()

    asyncio.run(scenario())


def test_restore_removes_activation_when_content_digest_changes(tmp_path) -> None:
    path = tmp_path / "state.db"
    source = MutableSkillSource()
    policy = MutableTrustPolicy()

    async def scenario() -> None:
        store = SQLiteSkillActivationStore(path)
        runtime = await _runtime(source, policy, store)
        await runtime.load("session-a", "review")
        store.close()

        source.content_suffix = "\nchanged"
        reopened = SQLiteSkillActivationStore(path)
        restored = await _runtime(source, policy, reopened)
        assert await restored.restore("session-a") == ()
        assert reopened.list("session-a") == ()
        reopened.close()

    asyncio.run(scenario())


def test_restore_removes_activation_when_policy_decision_changes(tmp_path) -> None:
    path = tmp_path / "state.db"
    source = MutableSkillSource()
    policy = MutableTrustPolicy()

    async def scenario() -> None:
        store = SQLiteSkillActivationStore(path)
        runtime = await _runtime(source, policy, store)
        await runtime.load("session-a", "review")
        store.close()

        policy.policy_id = "policy-v2"
        reopened = SQLiteSkillActivationStore(path)
        restored = await _runtime(source, policy, reopened)
        assert await restored.restore("session-a") == ()
        assert reopened.list("session-a") == ()
        reopened.close()

    asyncio.run(scenario())


def test_disable_and_close_session_delete_durable_activations(tmp_path) -> None:
    source = MutableSkillSource()
    policy = MutableTrustPolicy()

    async def scenario() -> None:
        store = SQLiteSkillActivationStore(tmp_path / "state.db")
        runtime = await _runtime(source, policy, store)
        await runtime.load("session-a", "review")
        assert runtime.disable("session-a", "review") is True
        assert store.list("session-a") == ()

        await runtime.load("session-a", "review")
        runtime.close_session("session-a")
        assert store.list("session-a") == ()
        store.close()

    asyncio.run(scenario())


def test_store_rejects_malformed_schema_at_open(tmp_path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE agentos_skill_activations (session_id TEXT PRIMARY KEY)"
        )

    with pytest.raises(
        SkillActivationCorruptedError,
        match="^skill activation store schema is corrupted$",
    ):
        SQLiteSkillActivationStore(path)


def test_store_rejects_unsupported_component_version(tmp_path) -> None:
    path = tmp_path / "state.db"
    SQLiteSkillActivationStore(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE agentos_skill_activation_schema SET version = 2"
        )

    with pytest.raises(
        SkillActivationCorruptedError,
        match="^skill activation store schema is unsupported$",
    ):
        SQLiteSkillActivationStore(path)


def test_store_rejects_schema_without_required_composite_uniqueness(
    tmp_path,
) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE agentos_skill_activation_schema ("
            "version INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO agentos_skill_activation_schema (version) VALUES (1)"
        )
        connection.execute(
            """
            CREATE TABLE agentos_skill_activations (
                session_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                policy_id TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX wrong_activation_identity "
            "ON agentos_skill_activations (skill_name, session_id)"
        )

    with pytest.raises(
        SkillActivationCorruptedError,
        match="^skill activation store schema is corrupted$",
    ):
        SQLiteSkillActivationStore(path)


def test_corrupted_activation_error_does_not_echo_stored_values(tmp_path) -> None:
    path = tmp_path / "state.db"
    source = MutableSkillSource()
    policy = MutableTrustPolicy()

    async def activate() -> None:
        store = SQLiteSkillActivationStore(path)
        runtime = await _runtime(source, policy, store)
        await runtime.load("session-a", "review")
        store.close()

    asyncio.run(activate())
    secret = "C:\\private\\api_key=secret-value-must-not-leak"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE agentos_skill_activations SET skill_name = ?",
            (secret,),
        )

    with SQLiteSkillActivationStore(path) as store:
        with pytest.raises(
            SkillActivationCorruptedError,
            match="^stored skill activation is corrupted$",
        ) as caught:
            store.list("session-a")

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert secret not in rendered
    assert "invalid skill name" not in rendered


def test_sqlite_corruption_is_mapped_without_underlying_error_chain(tmp_path) -> None:
    path = tmp_path / "private" / "state.db"
    path.parent.mkdir()
    path.write_bytes(b"secret-value-must-not-leak")

    with pytest.raises(
        SkillActivationCorruptedError,
        match="^skill activation store schema is corrupted$",
    ) as caught:
        SQLiteSkillActivationStore(path)

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert "file is not a database" not in rendered
    assert str(path) not in rendered
    assert "secret-value-must-not-leak" not in rendered


def test_store_close_is_idempotent_and_final(tmp_path) -> None:
    store = SQLiteSkillActivationStore(tmp_path / "state.db")
    record = SkillActivationRecord(
        session_id="session-a",
        subject=MutableSkillSource()._subject(),
        policy_id="policy-v1",
    )
    store.close()
    store.close()

    operations = (
        lambda: store.save(record),
        lambda: store.list("session-a"),
        lambda: store.delete("session-a", "review"),
        lambda: store.delete_session("session-a"),
        store.__enter__,
    )
    for operation in operations:
        with pytest.raises(
            SkillActivationStoreClosedError,
            match="^SQLiteSkillActivationStore is closed$",
        ):
            operation()
