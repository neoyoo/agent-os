from __future__ import annotations

from agentos.artifacts import ArtifactNotFoundError
from agentos.capabilities.result_refs import ArtifactToolResultRef, ToolResultRef
from agentos.distributed.errors import ArtifactInUseError
from agentos.distributed.postgres._database import AsyncConnection, fetchone


async def require_artifact_unreferenced(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    artifact_id: str,
) -> None:
    row = await fetchone(
        connection,
        """
        SELECT EXISTS (
            SELECT 1
            FROM agentos_distributed_accepted_inputs
            WHERE tenant_id = %s AND session_id = %s
              AND status IN ('accepted', 'claimed')
              AND artifact_handles ? %s
            UNION ALL
            SELECT 1
            FROM agentos_distributed_accepted_inputs
            WHERE tenant_id = %s AND session_id = %s
              AND continuation_kind = 'resolve_side_effect'
              AND status IN ('accepted', 'claimed')
              AND payload_json::jsonb #>> '{result_ref,artifact_id}' = %s
            UNION ALL
            SELECT 1
            FROM agentos_distributed_checkpoints AS checkpoint
            CROSS JOIN LATERAL jsonb_array_elements(
                (checkpoint.snapshot_json::jsonb)->'messages'
            ) AS message
            CROSS JOIN LATERAL jsonb_array_elements(
                message->'artifact_refs'
            ) AS artifact_ref
            WHERE checkpoint.tenant_id = %s
              AND checkpoint.session_id = %s
              AND checkpoint.snapshot_json IS NOT NULL
              AND artifact_ref->>'artifact_id' = %s
            UNION ALL
            SELECT 1
            FROM agentos_distributed_side_effects
            WHERE tenant_id = %s AND session_id = %s
              AND payload_json::jsonb #>> '{result_ref,artifact_id}' = %s
        ) AS referenced
        """,
        (
            tenant_id,
            session_id,
            artifact_id,
            tenant_id,
            session_id,
            artifact_id,
            tenant_id,
            session_id,
            artifact_id,
            tenant_id,
            session_id,
            artifact_id,
        ),
    )
    if row is None or row["referenced"] is not False:
        raise ArtifactInUseError()


async def require_active_artifact_result(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    reference: ToolResultRef | None,
) -> None:
    if type(reference) is not ArtifactToolResultRef:
        return
    row = await fetchone(
        connection,
        """
        SELECT artifact_id FROM agentos_distributed_artifacts
        WHERE tenant_id = %s AND session_id = %s AND artifact_id = %s
          AND lifecycle = 'active'
        FOR SHARE
        """,
        (tenant_id, session_id, reference.artifact.artifact_id),
    )
    if row is None:
        raise ArtifactNotFoundError()


__all__ = ["require_active_artifact_result", "require_artifact_unreferenced"]
