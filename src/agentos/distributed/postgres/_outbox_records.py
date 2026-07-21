from __future__ import annotations

from collections.abc import Mapping
import json

from agentos.distributed.models import RequestScope
from agentos.distributed.postgres.a2a_delivery import fanout_status_push_deliveries
from agentos.distributed.postgres._database import AsyncConnection
from agentos.distributed.postgres._database import fetchone
from agentos.distributed.postgres._identities import outbox_id


EXECUTION_TOPIC = "agentos.run.execution"
STATUS_TOPIC = "agentos.run.status"


async def insert_outbox(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    source_kind: str,
    source_id: str,
    topic: str,
    payload: Mapping[str, object],
) -> str:
    identifier = outbox_id(scope.tenant_id, source_kind, source_id)
    frozen_payload = {
        "kind": source_kind,
        "outbox_id": identifier,
        "run_id": run_id,
        "session_id": session_id,
        "version": 1,
        **payload,
    }
    inserted = await fetchone(
        connection,
        """
        INSERT INTO agentos_distributed_outbox
            (outbox_id, tenant_id, principal_id, session_id, run_id, topic, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (outbox_id) DO NOTHING
        RETURNING outbox_id
        """,
        (
            identifier,
            scope.tenant_id,
            scope.principal_id,
            session_id,
            run_id,
            topic,
            _canonical_json(frozen_payload),
        ),
    )
    if inserted is not None and topic == STATUS_TOPIC:
        await fanout_status_push_deliveries(
            connection,
            scope=scope,
            session_id=session_id,
            run_id=run_id,
            event_id=identifier,
            payload=payload,
        )
    return identifier


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


__all__ = ["EXECUTION_TOPIC", "STATUS_TOPIC", "insert_outbox"]
