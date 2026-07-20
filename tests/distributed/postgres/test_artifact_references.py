from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agentos._waiting import WaitReason
from agentos.artifacts import ArtifactNotFoundError, ArtifactRef
from agentos.capabilities.result_refs import ArtifactToolResultRef
from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.errors import ArtifactInUseError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres import _commands
from agentos.distributed.postgres._checkpoint_records import write_checkpoint
from agentos.distributed.postgres._state_records import require_artifacts
from agentos.distributed.postgres._artifact_references import (
    require_active_artifact_result,
    require_artifact_unreferenced,
)
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres._side_effect_codec import side_effect_record_to_json
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectStatus,
)
from tests.durable._fixtures import checkpoint_source
from tests.planning._async import async_test


ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"
SCOPE = RequestScope("tenant_1", "principal_1")


class _Cursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row

    async def fetchall(self) -> list[dict[str, object]]:
        return [] if self._row is None else [self._row]


class _ReferencedArtifactDatabase:
    def __init__(self) -> None:
        self.mutations: list[str] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> _Cursor:
        del params
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            return _Cursor()
        if normalized.startswith(
            "SELECT session_id FROM agentos_distributed_sessions"
        ):
            return _Cursor({"session_id": "session_1"})
        if normalized.startswith(
            "SELECT session_id, artifact_id FROM "
            "agentos_distributed_artifact_deletions"
        ):
            return _Cursor()
        if normalized.startswith("SELECT * FROM agentos_distributed_artifacts"):
            return _Cursor(
                {
                    "tenant_id": SCOPE.tenant_id,
                    "session_id": "session_1",
                    "artifact_id": ARTIFACT_ID,
                    "upload_id": "upload_1",
                    "filename": "drawing.png",
                    "media_type": "image/png",
                    "size_bytes": 7,
                    "content_digest": "digest",
                    "blob_key": ARTIFACT_ID,
                    "lifecycle": "active",
                    "deletion_id": None,
                    "created_at": datetime(2026, 7, 20, tzinfo=UTC),
                }
            )
        if normalized.startswith("SELECT EXISTS"):
            return _Cursor({"referenced": True})
        self.mutations.append(normalized)
        return _Cursor()

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self


class _Blobs:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, *, artifact_id: str) -> None:
        self.deleted.append(artifact_id)

    async def close(self) -> None:
        return None


@async_test
async def test_delete_rejects_artifact_referenced_by_durable_state() -> None:
    database = _ReferencedArtifactDatabase()
    blobs = _Blobs()
    store = PostgresArtifactStore(database, blobs)  # type: ignore[arg-type]

    with pytest.raises(ArtifactInUseError):
        await store.delete(
            scope=SCOPE,
            session_id="session_1",
            artifact_id=ARTIFACT_ID,
            deletion_id="deletion_1",
        )

    assert database.mutations == []
    assert blobs.deleted == []


@async_test
async def test_submission_artifact_validation_locks_referenced_rows() -> None:
    queries: list[str] = []

    class _Connection:
        async def execute(
            self,
            query: str,
            params: Sequence[object] = (),
        ) -> _Cursor:
            del params
            queries.append(" ".join(query.split()))
            return _Cursor({"artifact_id": ARTIFACT_ID})

    await require_artifacts(
        _Connection(),  # type: ignore[arg-type]
        SCOPE,
        "session_1",
        (ARTIFACT_ID,),
    )

    assert queries[0].endswith("FOR SHARE")


@async_test
async def test_artifact_result_ref_requires_active_locked_metadata() -> None:
    queries: list[str] = []

    class _Connection:
        async def execute(
            self,
            query: str,
            params: Sequence[object] = (),
        ) -> _Cursor:
            del params
            queries.append(" ".join(query.split()))
            return _Cursor()

    reference = ArtifactToolResultRef(
        ArtifactRef(ARTIFACT_ID, "tool-result.txt", "text/plain"),
        "preview",
    )
    with pytest.raises(ArtifactNotFoundError):
        await require_active_artifact_result(
            _Connection(),  # type: ignore[arg-type]
            tenant_id=SCOPE.tenant_id,
            session_id="session_1",
            reference=reference,
        )

    assert queries[0].endswith("FOR SHARE")


@async_test
async def test_checkpoint_locks_all_message_artifacts_before_insert() -> None:
    queries: list[str] = []
    checkpoint = checkpoint_source("session_1").capture()
    checkpoint = replace(
        checkpoint,
        messages=(
            replace(
                checkpoint.messages[0],
                artifact_refs=(ArtifactRef(ARTIFACT_ID, "drawing.png", "image/png"),),
            ),
        ),
    )

    class _Connection:
        async def execute(
            self,
            query: str,
            params: Sequence[object] = (),
        ) -> _Cursor:
            del params
            normalized = " ".join(query.split())
            queries.append(normalized)
            if normalized.startswith("SELECT snapshot_json"):
                return _Cursor()
            if "FROM agentos_distributed_artifacts" in normalized:
                return _Cursor({"artifact_id": ARTIFACT_ID})
            if normalized.startswith("INSERT INTO agentos_distributed_checkpoints"):
                return _Cursor({"created_at": datetime(2026, 7, 20, tzinfo=UTC)})
            return _Cursor()

    await write_checkpoint(  # type: ignore[arg-type]
        _Connection(),
        scope=SCOPE,
        checkpoint=checkpoint,
        run_id="run_1",
        turn_id="turn_1",
        aggregate_version=1,
        fencing_token=7,
    )

    artifact_query = next(
        index
        for index, query in enumerate(queries)
        if "FROM agentos_distributed_artifacts" in query
    )
    checkpoint_insert = next(
        index
        for index, query in enumerate(queries)
        if query.startswith("INSERT INTO agentos_distributed_checkpoints")
    )
    assert queries[artifact_query].endswith("FOR SHARE")
    assert artifact_query < checkpoint_insert


@async_test
async def test_artifact_resolution_command_locks_active_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []
    operation_id = "operation_0123456789abcdef0123456789abcdef"
    reference = ArtifactToolResultRef(
        ArtifactRef(ARTIFACT_ID, "tool-result.txt", "text/plain"),
        "preview",
    )
    resolution = SideEffectResolution(
        operation_id,
        SideEffectResolutionKind.ACCEPT_RESULT,
        result_ref=reference,
        result_digest=result_ref_digest(reference),
    )
    record = SideEffectRecord(
        SideEffectAttemptId(SCOPE.tenant_id, "session_1", operation_id, 1),
        "run_1",
        "turn_1",
        "invocation_0123456789abcdef0123456789abcdef",
        "lookup",
        SideEffectPolicy.NON_RETRYABLE,
        SideEffectStatus.AMBIGUOUS,
        "sha256:" + "a" * 64,
        invocation_ref=ProtectedPayloadRef("sealed", "digest"),
        claim_id="claim_1",
        fencing_token=7,
    )

    class _Connection:
        async def execute(
            self,
            query: str,
            params: Sequence[object] = (),
        ) -> _Cursor:
            del params
            normalized = " ".join(query.split())
            queries.append(normalized)
            if "FROM agentos_distributed_side_effects" in normalized:
                return _Cursor(
                    {
                        "tenant_id": SCOPE.tenant_id,
                        "session_id": "session_1",
                        "operation_id": operation_id,
                        "attempt": 1,
                        "run_id": "run_1",
                        "status": "ambiguous",
                        "payload_json": side_effect_record_to_json(record),
                    },
                )
            if "FROM agentos_distributed_artifacts" in normalized:
                return _Cursor({"artifact_id": ARTIFACT_ID})
            raise AssertionError(f"unexpected query: {normalized}")

    async def _valid_source(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(
        _commands,
        "validate_reconciliation_command_source",
        _valid_source,
    )
    await _commands._validate_resolution(  # type: ignore[arg-type]
        _Connection(),
        SCOPE,
        RunState(
            "run_1",
            "session_1",
            RunStatus.WAITING,
            WaitReason("side_effect_reconciliation", operation_id),
            3,
        ),
        DurableRunCommand("run_1", "command_1", "resolve_side_effect", resolution),
    )

    artifact_query = next(
        query for query in queries if "FROM agentos_distributed_artifacts" in query
    )
    assert artifact_query.endswith("FOR SHARE")


@async_test
async def test_pending_resolution_artifact_is_a_durable_pin() -> None:
    queries: list[str] = []

    class _Connection:
        async def execute(
            self,
            query: str,
            params: Sequence[object] = (),
        ) -> _Cursor:
            del params
            normalized = " ".join(query.split())
            queries.append(normalized)
            return _Cursor({"referenced": False})

    await require_artifact_unreferenced(  # type: ignore[arg-type]
        _Connection(),
        tenant_id=SCOPE.tenant_id,
        session_id="session_1",
        artifact_id=ARTIFACT_ID,
    )

    assert "continuation_kind = 'resolve_side_effect'" in queries[0]
    assert "payload_json::jsonb #>> '{result_ref,artifact_id}'" in queries[0]
