from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json
from typing import cast

from agentos.distributed.postgres._database import Row
from agentos.multi.team_delivery_types import TeamDelivery, TeamDeliveryResult
from agentos.multi.team_event_types import (
    TeamDeliveryAppliedEvent,
    TeamDeliveryRejectedEvent,
    TeamEventEnvelope,
)
from agentos.multi.team_types import (
    TeamMemberRecord,
    TeamMessage,
    TeamRecipient,
    TeamRecord,
)
from agentos.runtime.run_state import RunStatus
from agentos.workspace.models import WorkspaceHandle


def team_from_row(row: Row, workspace: WorkspaceHandle | None) -> TeamRecord:
    return TeamRecord(
        team_id=_text(row, "team_id"),
        leader_agent_id=_text(row, "leader_agent_id"),
        workspace=workspace,
        created_at=_datetime(row, "created_at"),
        status=cast(str, _text(row, "status")),  # type: ignore[arg-type]
        deleted_at=_optional_datetime(row, "deleted_at"),
    )


def member_from_row(row: Row) -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id=_text(row, "team_id"),
        recipient_agent_id=_text(row, "recipient_agent_id"),
        role=cast(str, _text(row, "role")),  # type: ignore[arg-type]
        target_session_id=_text(row, "target_session_id"),
        capabilities=tuple(_string_list(row, "capabilities_json")),
        created_at=_datetime(row, "created_at"),
        status=cast(str, _text(row, "status")),  # type: ignore[arg-type]
        deleted_at=_optional_datetime(row, "deleted_at"),
    )


def message_from_row(row: Row, *, prefix: str = "") -> TeamMessage:
    def field(name: str) -> str:
        return prefix + name

    recipients = tuple(
        TeamRecipient(
            _mapping_text(item, "recipient_agent_id"),
            _mapping_text(item, "target_session_id"),
        )
        for item in _mapping_list(row, field("recipient_snapshot_json"))
    )
    return TeamMessage(
        message_id=_text(row, "message_id"),
        team_id=_text(row, "team_id"),
        sender_agent_id=_text(row, field("sender_agent_id")),
        operation_id=_text(row, field("operation_id")),
        message_kind=cast(  # type: ignore[arg-type]
            str,
            _text(row, field("message_kind")),
        ),
        content=_text(row, field("content")),
        correlation_id=_optional_text(row, field("correlation_id")),
        addressing_kind=cast(  # type: ignore[arg-type]
            str,
            _text(row, field("addressing_kind")),
        ),
        addressed_agent_id=_optional_text(row, field("addressed_agent_id")),
        recipient_snapshot=recipients,
        request_sha256=_text(row, field("request_sha256")),
        created_at=_datetime(row, field("created_at")),
    )


def delivery_from_row(row: Row) -> TeamDelivery:
    status = _optional_text(row, "observed_run_status")
    return TeamDelivery(
        delivery_id=_text(row, "delivery_id"),
        team_id=_text(row, "team_id"),
        message_id=_text(row, "message_id"),
        recipient_agent_id=_text(row, "recipient_agent_id"),
        target_session_id=_text(row, "target_session_id"),
        state=cast(str, _text(row, "state")),  # type: ignore[arg-type]
        fencing_token=_integer(row, "fencing_token"),
        source_sha256=_text(row, "source_sha256"),
        created_at=_datetime(row, "created_at"),
        updated_at=_datetime(row, "updated_at"),
        claim_id=_optional_text(row, "claim_id"),
        claim_expires_at=_optional_datetime(row, "claim_expires_at"),
        result_kind=cast(  # type: ignore[arg-type]
            str | None,
            _optional_text(row, "result_kind"),
        ),
        observed_run_id=_optional_text(row, "observed_run_id"),
        observed_aggregate_version=_optional_integer(
            row,
            "observed_aggregate_version",
        ),
        observed_run_status=None if status is None else RunStatus(status),
    )


def result_payload(delivery_id: str, result: TeamDeliveryResult) -> dict[str, object]:
    return {
        "delivery_id": delivery_id,
        "result": {
            "result_kind": result.result_kind,
            "observed_run_id": result.observed_run_id,
            "observed_aggregate_version": result.observed_aggregate_version,
            "observed_run_status": (
                None
                if result.observed_run_status is None
                else result.observed_run_status.value
            ),
        },
    }


def event_from_row(row: Row) -> TeamEventEnvelope:
    payload = _mapping(row, "payload_json")
    result_value = payload.get("result")
    if not isinstance(result_value, Mapping):
        raise TypeError("event result must be an object")
    status = _mapping_optional_text(result_value, "observed_run_status")
    result = TeamDeliveryResult(
        result_kind=cast(  # type: ignore[arg-type]
            str,
            _mapping_text(result_value, "result_kind"),
        ),
        observed_run_id=_mapping_optional_text(result_value, "observed_run_id"),
        observed_aggregate_version=_mapping_optional_integer(
            result_value,
            "observed_aggregate_version",
        ),
        observed_run_status=None if status is None else RunStatus(status),
    )
    delivery_id = _mapping_text(payload, "delivery_id")
    event_kind = _text(row, "event_kind")
    if event_kind == "delivery_applied":
        event = TeamDeliveryAppliedEvent(delivery_id, result)
    elif event_kind == "delivery_rejected":
        event = TeamDeliveryRejectedEvent(delivery_id, result)
    else:
        raise ValueError("event_kind is invalid")
    return TeamEventEnvelope(
        tenant_id=_text(row, "tenant_id"),
        team_id=_text(row, "team_id"),
        event_sequence=_integer(row, "event_sequence"),
        event=event,
        occurred_at=_datetime(row, "created_at"),
    )


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def recipient_snapshot_json(recipients: tuple[TeamRecipient, ...]) -> str:
    return canonical_json(
        [
            {
                "recipient_agent_id": item.recipient_agent_id,
                "target_session_id": item.target_session_id,
            }
            for item in recipients
        ],
    )


def _json_value(value: object) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _mapping(row: Row, field_name: str) -> Mapping[str, object]:
    value = _json_value(row[field_name])
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return value


def _mapping_list(row: Row, field_name: str) -> tuple[Mapping[str, object], ...]:
    value = _json_value(row[field_name])
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise TypeError(f"{field_name} must be an array of objects")
    return tuple(cast(Mapping[str, object], item) for item in value)


def _string_list(row: Row, field_name: str) -> tuple[str, ...]:
    value = _json_value(row[field_name])
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise TypeError(f"{field_name} must be an array of strings")
    return tuple(cast(str, item) for item in value)


def _text(row: Row, field_name: str) -> str:
    value = row[field_name]
    if type(value) is not str:
        raise TypeError(f"{field_name} must be str")
    return value


def _optional_text(row: Row, field_name: str) -> str | None:
    value = row[field_name]
    if value is not None and type(value) is not str:
        raise TypeError(f"{field_name} must be str or None")
    return cast(str | None, value)


def _integer(row: Row, field_name: str) -> int:
    value = row[field_name]
    if type(value) is not int:
        raise TypeError(f"{field_name} must be int")
    return value


def _optional_integer(row: Row, field_name: str) -> int | None:
    value = row[field_name]
    if value is not None and type(value) is not int:
        raise TypeError(f"{field_name} must be int or None")
    return cast(int | None, value)


def _datetime(row: Row, field_name: str) -> datetime:
    value = row[field_name]
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError(f"{field_name} must be timezone-aware datetime")
    return value.astimezone(UTC)


def _optional_datetime(row: Row, field_name: str) -> datetime | None:
    value = row[field_name]
    return None if value is None else _datetime(row, field_name)


def _mapping_text(value: Mapping[str, object], field_name: str) -> str:
    item = value[field_name]
    if type(item) is not str:
        raise TypeError(f"{field_name} must be str")
    return item


def _mapping_optional_text(
    value: Mapping[str, object],
    field_name: str,
) -> str | None:
    item = value[field_name]
    if item is not None and type(item) is not str:
        raise TypeError(f"{field_name} must be str or None")
    return cast(str | None, item)


def _mapping_optional_integer(
    value: Mapping[str, object],
    field_name: str,
) -> int | None:
    item = value[field_name]
    if item is not None and type(item) is not int:
        raise TypeError(f"{field_name} must be int or None")
    return cast(int | None, item)


__all__ = [
    "canonical_json",
    "delivery_from_row",
    "event_from_row",
    "member_from_row",
    "message_from_row",
    "recipient_snapshot_json",
    "result_payload",
    "team_from_row",
]
