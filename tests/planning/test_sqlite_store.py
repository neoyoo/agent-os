from __future__ import annotations

import json
import sqlite3
import traceback
from dataclasses import replace
from pathlib import Path

import pytest

from agentos.planning.errors import PlanNotFoundError
from agentos.planning.models import (
    EvidenceHandle,
    PlanAssignment,
    PlanState,
    PlanStatus,
    PlanStep,
)
from agentos.planning.serializers import plan_state_to_dict
from agentos.planning.sqlite import (
    SQLitePlanStore,
    SQLitePlanStoreClosedError,
    SQLitePlanStoreCorruptedError,
    SQLitePlanStoreUnsafeError,
)
from agentos.workspace import WorkspaceHandle
from tests.planning._async import async_test


def _plan(
    plan_id: str,
    *,
    owner_agent_id: str = "agent_1",
    status: PlanStatus = "running",
) -> PlanState:
    return PlanState(
        plan_id=plan_id,
        objective="分析图纸并生成报价。",
        owner_agent_id=owner_agent_id,
        status=status,
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="读取原始附件。",
                status="completed",
                evidence_ids=("evidence_1",),
            ),
        ),
        evidence=(
            EvidenceHandle(
                evidence_id="evidence_1",
                kind="artifact",
                summary="原始图纸",
                metadata={"z": "末尾", "a": "开头"},
            ),
        ),
        assignments=(
            PlanAssignment(
                plan_id=plan_id,
                step_id="step_1",
                template_id="template_1",
                task_id="task_1",
                target_agent_id="agent_2",
                created_at=1.5,
            ),
        ),
        created_at=1.0,
        updated_at=2.0,
        workspace=WorkspaceHandle(
            workspace_id="workspace_1",
            scope="session",
            metadata={"purpose": "quotation"},
        ),
    )


@async_test
async def test_sqlite_plan_store_persists_create_get_and_list_order(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    first = _plan("plan_b")
    second = _plan("plan_a", owner_agent_id="agent_2")

    async with await SQLitePlanStore.open(database_path) as store:
        await store.create_plan(first)
        await store.create_plan(second)

        assert await store.get_plan("plan_b") == first
        assert await store.get_plan("missing") is None
        record = await store.get_plan_record("plan_b")
        assert record is not None
        assert record.revision == 0
        assert await store.list_plans() == [first, second]
        assert await store.list_plans(owner_agent_id="agent_2") == [second]

        with pytest.raises(ValueError, match="plan already exists: plan_b"):
            await store.create_plan(first)

    async with await SQLitePlanStore.open(database_path) as restarted:
        assert await restarted.list_plans() == [first, second]
        record = await restarted.get_plan_record("plan_b")
        assert record is not None
        assert record.revision == 0


@async_test
async def test_sqlite_plan_store_save_and_cas_are_revision_guarded(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    original = _plan("plan_1")
    first_store = await SQLitePlanStore.open(database_path)
    second_store = await SQLitePlanStore.open(database_path)
    try:
        await first_store.create_plan(original)
        first_record = await first_store.get_plan_record("plan_1")
        second_record = await second_store.get_plan_record("plan_1")
        assert first_record is not None
        assert second_record is not None

        completed = original.with_status("completed", now=3.0)
        assert await first_store.save_plan_if_unchanged(
            completed,
            expected_revision=first_record.revision,
        )
        assert not await second_store.save_plan_if_unchanged(
            original.with_status("failed", now=4.0),
            expected_revision=second_record.revision,
        )

        fresh = await second_store.get_plan_record("plan_1")
        assert fresh is not None
        assert fresh.plan == completed
        assert fresh.revision == 1

        cancelled = completed.with_status("cancelled", now=5.0)
        await second_store.save_plan(cancelled)
        saved = await first_store.get_plan_record("plan_1")
        assert saved is not None
        assert saved.plan == cancelled
        assert saved.revision == 2

        with pytest.raises(PlanNotFoundError):
            await first_store.save_plan(_plan("missing"))
        with pytest.raises(PlanNotFoundError):
            await first_store.save_plan_if_unchanged(
                _plan("missing"),
                expected_revision=0,
            )
    finally:
        await first_store.close()
        await second_store.close()


@async_test
async def test_sqlite_plan_store_writes_deterministic_canonical_json(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    plan = _plan("plan_1")
    async with await SQLitePlanStore.open(database_path) as store:
        await store.create_plan(plan)

    with sqlite3.connect(database_path) as connection:
        payload = connection.execute(
            "SELECT payload_json FROM agentos_plans WHERE plan_id = ?",
            (plan.plan_id,),
        ).fetchone()[0]

    assert payload == json.dumps(
        plan_state_to_dict(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert "分析图纸并生成报价" in payload
    assert "\\u5206\\u6790" not in payload


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        json.dumps(
            {
                **plan_state_to_dict(_plan("plan_1")),
                "owner_agent_id": 42,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    ],
)
@async_test
async def test_sqlite_plan_store_fails_closed_on_corrupted_payload(
    tmp_path: Path,
    payload: str,
) -> None:
    database_path = tmp_path / "state.db"
    async with await SQLitePlanStore.open(database_path) as store:
        await store.create_plan(_plan("plan_1"))

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agentos_plans SET payload_json = ? WHERE plan_id = ?",
            (payload, "plan_1"),
        )

    async with await SQLitePlanStore.open(database_path) as restarted:
        with pytest.raises(
            SQLitePlanStoreCorruptedError, match="^stored plan is corrupted$"
        ):
            await restarted.get_plan("plan_1")
        with pytest.raises(
            SQLitePlanStoreCorruptedError, match="^stored plan is corrupted$"
        ):
            await restarted.list_plans()


@async_test
async def test_sqlite_plan_store_rejects_row_and_payload_identity_mismatch(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    async with await SQLitePlanStore.open(database_path) as store:
        await store.create_plan(_plan("plan_1"))

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agentos_plans SET owner_agent_id = ? WHERE plan_id = ?",
            ("other_agent", "plan_1"),
        )

    async with await SQLitePlanStore.open(database_path) as restarted:
        with pytest.raises(
            SQLitePlanStoreCorruptedError, match="^stored plan is corrupted$"
        ):
            await restarted.get_plan_record("plan_1")


@pytest.mark.parametrize("compare_and_save", [False, True])
@async_test
async def test_sqlite_plan_store_does_not_overwrite_corrupted_records(
    tmp_path: Path,
    compare_and_save: bool,
) -> None:
    database_path = tmp_path / "state.db"
    async with await SQLitePlanStore.open(database_path) as store:
        await store.create_plan(_plan("plan_1"))

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agentos_plans SET payload_json = ? WHERE plan_id = ?",
            ("{not-json", "plan_1"),
        )

    async with await SQLitePlanStore.open(database_path) as store:
        with pytest.raises(
            SQLitePlanStoreCorruptedError, match="^stored plan is corrupted$"
        ):
            if compare_and_save:
                await store.save_plan_if_unchanged(
                    _plan("plan_1", status="completed"),
                    expected_revision=0,
                )
            else:
                await store.save_plan(_plan("plan_1", status="completed"))

    with sqlite3.connect(database_path) as connection:
        payload = connection.execute(
            "SELECT payload_json FROM agentos_plans WHERE plan_id = ?",
            ("plan_1",),
        ).fetchone()[0]
    assert payload == "{not-json"


@async_test
async def test_sqlite_plan_store_close_is_idempotent_and_blocks_further_use(
    tmp_path: Path,
) -> None:
    store = await SQLitePlanStore.open(tmp_path / "state.db")
    await store.create_plan(_plan("plan_1"))

    await store.close()
    await store.close()

    with pytest.raises(SQLitePlanStoreClosedError):
        await store.get_plan("plan_1")
    with pytest.raises(SQLitePlanStoreClosedError):
        await store.create_plan(_plan("plan_2"))
    with pytest.raises(SQLitePlanStoreClosedError):
        await store.__aenter__()


@async_test
async def test_sqlite_plan_store_rejects_unsafe_durable_fields_without_writing(
    tmp_path: Path,
) -> None:
    base = _plan("plan_1")
    unsafe = (
        replace(
            base,
            workspace=WorkspaceHandle(
                workspace_id="workspace_1",
                scope="session",
                root=str(tmp_path.resolve()),
            ),
        ),
        replace(
            base,
            evidence=(
                replace(
                    base.evidence[0],
                    uri="https://example.test/file?X-Amz-Signature=secret-value",
                ),
            ),
        ),
        replace(
            base,
            evidence=(replace(base.evidence[0], metadata={"api_key": "secret-value"}),),
        ),
    )

    async with await SQLitePlanStore.open(tmp_path / "state.db") as store:
        for plan in unsafe:
            with pytest.raises(
                SQLitePlanStoreUnsafeError,
                match="^plan contains unsafe durable data$",
            ):
                await store.create_plan(plan)
        assert await store.list_plans() == []


@async_test
async def test_sqlite_plan_store_unsafe_save_preserves_prior_revision(
    tmp_path: Path,
) -> None:
    original = _plan("plan_1")
    unsafe = replace(
        original,
        workspace=replace(original.workspace, metadata={"token": "secret"}),
    )
    async with await SQLitePlanStore.open(tmp_path / "state.db") as store:
        await store.create_plan(original)
        with pytest.raises(SQLitePlanStoreUnsafeError):
            await store.save_plan(unsafe)
        record = await store.get_plan_record("plan_1")
        assert record is not None
        assert record.plan == original
        assert record.revision == 0


@async_test
async def test_sqlite_plan_store_rejects_malformed_schema_at_open(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE agentos_plans (plan_id TEXT PRIMARY KEY, payload_json TEXT)"
        )

    with pytest.raises(
        SQLitePlanStoreCorruptedError,
        match="^plan store schema is corrupted$",
    ):
        await SQLitePlanStore.open(database_path)


@async_test
async def test_owner_filter_is_applied_before_deserializing_other_owners(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    owner_a = _plan("plan_a", owner_agent_id="agent_a")
    async with await SQLitePlanStore.open(database_path) as store:
        await store.create_plan(owner_a)
        await store.create_plan(_plan("plan_b", owner_agent_id="agent_b"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE agentos_plans SET payload_json = ? WHERE plan_id = ?",
            ("{corrupted", "plan_b"),
        )

    async with await SQLitePlanStore.open(database_path) as store:
        assert await store.list_plans(owner_agent_id="agent_a") == [owner_a]
        with pytest.raises(SQLitePlanStoreCorruptedError):
            await store.list_plans()


@async_test
async def test_corruption_error_traceback_does_not_echo_stored_values(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    secret = "secret-value-must-not-leak"
    async with await SQLitePlanStore.open(database_path) as store:
        await store.create_plan(_plan("plan_1"))
    with sqlite3.connect(database_path) as connection:
        payload = plan_state_to_dict(_plan("plan_1"))
        payload["status"] = secret
        connection.execute(
            "UPDATE agentos_plans SET payload_json = ? WHERE plan_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), "plan_1"),
        )

    async with await SQLitePlanStore.open(database_path) as store:
        with pytest.raises(SQLitePlanStoreCorruptedError) as caught:
            await store.get_plan("plan_1")
    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert secret not in rendered


@async_test
async def test_immediate_transaction_rolls_back_when_commit_fails() -> None:
    class FailingCommitConnection:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, _sql: str) -> None:
            self.calls.append("begin")

        async def commit(self) -> None:
            self.calls.append("commit")
            raise RuntimeError("commit failed")

        async def rollback(self) -> None:
            self.calls.append("rollback")

    connection = FailingCommitConnection()
    store = SQLitePlanStore(connection)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="commit failed"):
        async with store._transaction():
            pass
    assert connection.calls == ["begin", "commit", "rollback"]
