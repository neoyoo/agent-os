from __future__ import annotations

from datetime import datetime
from typing import cast

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import RequestScope
from agentos.distributed.redis._client import field_text
from agentos.multi.team_delivery_types import (
    TeamDeliveryResult,
    TeamDeliveryResultKind,
)
from agentos.multi.team_event_types import (
    TeamDeliveryAppliedEvent,
    TeamDeliveryRejectedEvent,
    TeamEventEnvelope,
)
from agentos.runtime.run_state import RunStatus


_EVENT_TYPES = {
    "delivery_applied": TeamDeliveryAppliedEvent,
    "delivery_rejected": TeamDeliveryRejectedEvent,
}


def encode_team_envelope(envelope: TeamEventEnvelope) -> dict[str, object]:
    """把 allowlisted Team event 编码为不含消息正文的 Redis fields。"""

    event = envelope.event
    result = event.result
    return {
        "tenant_id": envelope.tenant_id,
        "team_id": envelope.team_id,
        "event_sequence": str(envelope.event_sequence),
        "event_kind": envelope.event_kind,
        "delivery_id": event.delivery_id,
        "result_kind": result.result_kind,
        "observed_run_id": result.observed_run_id or "",
        "observed_aggregate_version": (
            ""
            if result.observed_aggregate_version is None
            else str(result.observed_aggregate_version)
        ),
        "observed_run_status": (
            "" if result.observed_run_status is None else result.observed_run_status.value
        ),
        "occurred_at": envelope.occurred_at.isoformat(),
    }


def decode_scoped_team_envelope(
    value: object,
    scope: RequestScope,
    team_id: str,
) -> TeamEventEnvelope:
    """解码 Redis fields，并重新验证 tenant/team scope 与领域类型。"""

    envelope = _decode_team_envelope(value)
    if envelope.tenant_id != scope.tenant_id or envelope.team_id != team_id:
        raise DeliveryUnavailableError()
    return envelope


def _decode_team_envelope(value: object) -> TeamEventEnvelope:
    try:
        kind = _required_field(value, "event_kind")
        result_kind = _required_field(value, "result_kind")
        run_id = _optional_field(value, "observed_run_id")
        version_text = _optional_field(value, "observed_aggregate_version")
        status_text = _optional_field(value, "observed_run_status")
        result = TeamDeliveryResult(
            result_kind=cast(TeamDeliveryResultKind, result_kind),
            observed_run_id=run_id,
            observed_aggregate_version=(
                None if version_text is None else int(version_text)
            ),
            observed_run_status=(
                None if status_text is None else RunStatus(status_text)
            ),
        )
        event_type = _EVENT_TYPES[kind]
        event = event_type(
            delivery_id=_required_field(value, "delivery_id"),
            result=result,
        )
        return TeamEventEnvelope(
            tenant_id=_required_field(value, "tenant_id"),
            team_id=_required_field(value, "team_id"),
            event_sequence=int(_required_field(value, "event_sequence")),
            event=event,
            occurred_at=datetime.fromisoformat(_required_field(value, "occurred_at")),
        )
    except (KeyError, TypeError, ValueError):
        raise DeliveryUnavailableError() from None


def _required_field(value: object, name: str) -> str:
    result = field_text(value, name)
    if not result:
        raise ValueError
    return result


def _optional_field(value: object, name: str) -> str | None:
    return field_text(value, name) or None


__all__ = ["decode_scoped_team_envelope", "encode_team_envelope"]
