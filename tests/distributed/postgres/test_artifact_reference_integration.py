from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from uuid import uuid4

import pytest

from agentos._json_values import thaw_json_value
from agentos.artifacts import ArtifactRef
from agentos.capabilities.result_refs import ArtifactToolResultRef
from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.errors import ArtifactInUseError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.postgres._checkpoint_records import write_checkpoint
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres._side_effect_codec import side_effect_record_to_json
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.durable.serialization import dump_json
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_resolution import side_effect_resolution_to_payload
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectStatus,
)
from tests.durable._fixtures import checkpoint_source


class _LiveBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
        if artifact_id in self.objects:
            return False
        self.objects[artifact_id] = data
        return True

    async def read(self, *, artifact_id: str) -> bytes | None:
        return self.objects.get(artifact_id)

    async def delete(self, *, artifact_id: str) -> None:
        self.objects.pop(artifact_id, None)

    async def close(self) -> None:
        return None


@pytest.mark.integration
def test_live_postgres_rejects_all_durable_artifact_pins() -> None:
    if not os.environ.get("AGENTOS_RUN_INTEGRATION"):
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 with a PostgreSQL test service")
    dsn = os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set AGENTOS_TEST_POSTGRES_DSN")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_live_durable_pins(dsn))


async def _verify_live_durable_pins(dsn: str) -> None:
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_pin_{suffix}", "principal_1")
    session_id = f"session_pin_{suffix}"
    database = await PostgresPool.open(dsn, min_size=0, max_size=2)
    blobs = _LiveBlobs()
    artifacts = PostgresArtifactStore(database, blobs)
    try:
        state = PostgresStateStore(database)
        await DistributedMigrationService(
            port=PostgresMigrationPort(database),
            plan=canonical_migration_plan(),
        ).apply()
        artifact = await artifacts.upload(
            scope=scope,
            session_id=session_id,
            upload_id=f"upload_{suffix}",
            data=b"durable",
            filename="drawing.txt",
            media_type="text/plain",
        )
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "inspect",
                (artifact.id,),
            ),
        )
        await _assert_live_delete_rejected(
            artifacts,
            scope,
            session_id,
            artifact.id,
            f"delete_accepted_{suffix}",
        )

        async with database.transaction() as connection:
            await connection.execute(
                """
                UPDATE agentos_distributed_accepted_inputs
                SET status = 'committed'
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                """,
                (scope.tenant_id, session_id, receipt.run_id),
            )
            checkpoint = checkpoint_source(session_id).capture()
            message = replace(
                checkpoint.messages[0],
                artifact_refs=(
                    ArtifactRef(artifact.id, artifact.filename, artifact.media_type),
                ),
            )
            await write_checkpoint(
                connection,
                scope=scope,
                checkpoint=replace(checkpoint, messages=(message,)),
                run_id=receipt.run_id,
                turn_id="turn_1",
                aggregate_version=1,
                fencing_token=0,
            )
        await _assert_live_delete_rejected(
            artifacts,
            scope,
            session_id,
            artifact.id,
            f"delete_checkpoint_{suffix}",
        )

        reference, record = await _pin_with_ledger(
            database,
            scope,
            session_id,
            receipt.run_id,
            artifact.id,
            artifact.filename,
            artifact.media_type,
            suffix,
        )
        await _assert_live_delete_rejected(
            artifacts,
            scope,
            session_id,
            artifact.id,
            f"delete_ledger_{suffix}",
        )
        await _replace_ledger_with_pending_resolution(
            database,
            scope,
            session_id,
            receipt.run_id,
            reference,
            record,
            suffix,
        )
        await _assert_live_delete_rejected(
            artifacts,
            scope,
            session_id,
            artifact.id,
            f"delete_resolution_{suffix}",
        )
        assert blobs.objects[artifact.id] == b"durable"
    finally:
        await _clean_live_state(database, scope.tenant_id)
        await database.close()


async def _pin_with_ledger(
    database: PostgresPool,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    artifact_id: str,
    filename: str | None,
    media_type: str,
    suffix: str,
) -> tuple[ArtifactToolResultRef, SideEffectRecord]:
    reference = ArtifactToolResultRef(
        ArtifactRef(artifact_id, filename, media_type),
        "preview",
    )
    record = SideEffectRecord(
        attempt_id=SideEffectAttemptId(
            scope.tenant_id,
            session_id,
            f"operation_{suffix}",
            1,
        ),
        run_id=run_id,
        turn_id="turn_1",
        invocation_id=f"invocation_{suffix}",
        tool_name="lookup",
        policy=SideEffectPolicy.PURE,
        status=SideEffectStatus.COMPLETED,
        invocation_digest="sha256:" + "a" * 64,
        invocation_ref=ProtectedPayloadRef("sealed", "digest"),
        result_ref=reference,
        result_digest=result_ref_digest(reference),
        outcome_kind=SideEffectOutcomeKind.PROVIDER_RESULT,
        claim_id="claim_1",
        fencing_token=1,
    )
    async with database.transaction() as connection:
        await connection.execute(
            "DELETE FROM agentos_distributed_checkpoints WHERE tenant_id = %s",
            (scope.tenant_id,),
        )
        await connection.execute(
            """
            INSERT INTO agentos_distributed_side_effects
                (tenant_id, session_id, operation_id, attempt, run_id,
                 status, payload_json)
            VALUES (%s, %s, %s, 1, %s, 'completed', %s)
            """,
            (
                scope.tenant_id,
                session_id,
                record.attempt_id.operation_id,
                run_id,
                side_effect_record_to_json(record),
            ),
        )
    return reference, record


async def _replace_ledger_with_pending_resolution(
    database: PostgresPool,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    reference: ArtifactToolResultRef,
    record: SideEffectRecord,
    suffix: str,
) -> None:
    resolution = SideEffectResolution(
        record.attempt_id.operation_id,
        SideEffectResolutionKind.ACCEPT_RESULT,
        result_ref=reference,
        result_digest=result_ref_digest(reference),
    )
    async with database.transaction() as connection:
        await connection.execute(
            "DELETE FROM agentos_distributed_side_effects WHERE tenant_id = %s",
            (scope.tenant_id,),
        )
        await connection.execute(
            """
            INSERT INTO agentos_distributed_accepted_inputs
                (tenant_id, principal_id, session_id, run_id, turn_id,
                 source_kind, source_id, continuation_kind, payload_json, status)
            VALUES (%s, %s, %s, %s, 'turn_2', 'command', %s,
                    'resolve_side_effect', %s, 'accepted')
            """,
            (
                scope.tenant_id,
                scope.principal_id,
                session_id,
                run_id,
                f"command_{suffix}",
                dump_json(
                    thaw_json_value(
                        side_effect_resolution_to_payload(resolution),
                    ),
                ),
            ),
        )


async def _clean_live_state(database: PostgresPool, tenant_id: str) -> None:
    async with database.transaction() as connection:
        for table in (
            "agentos_distributed_side_effects",
            "agentos_distributed_checkpoints",
            "agentos_distributed_outbox",
            "agentos_distributed_accepted_inputs",
            "agentos_distributed_submissions",
            "agentos_distributed_runs",
            "agentos_distributed_artifact_deletions",
            "agentos_distributed_artifacts",
            "agentos_distributed_sessions",
        ):
            await connection.execute(
                f"DELETE FROM {table} WHERE tenant_id = %s",
                (tenant_id,),
            )


async def _assert_live_delete_rejected(
    store: PostgresArtifactStore,
    scope: RequestScope,
    session_id: str,
    artifact_id: str,
    deletion_id: str,
) -> None:
    with pytest.raises(ArtifactInUseError):
        await store.delete(
            scope=scope,
            session_id=session_id,
            artifact_id=artifact_id,
            deletion_id=deletion_id,
        )
