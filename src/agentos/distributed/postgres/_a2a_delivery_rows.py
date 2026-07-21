from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from agentos.distributed.a2a_models import A2APushDeliveryTarget, A2ATaskState
from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import Row
from agentos.runtime.payloads import ProtectedPayloadRef


def push_delivery_target_from_row(row: Row) -> A2APushDeliveryTarget:
    """把 PostgreSQL delivery 行解码为严格的不可变发送目标。"""

    try:
        token = optional_text(row, "secret_token")
        digest = optional_text(row, "secret_digest")
        if (token is None) != (digest is None):
            raise ValueError("invalid protected secret")
        return A2APushDeliveryTarget(
            scope=RequestScope(text(row, "tenant_id"), text(row, "principal_id")),
            outbox_id=text(row, "outbox_id"),
            delivery_id=text(row, "delivery_id"),
            task_id=text(row, "task_id"),
            context_id=text(row, "context_id"),
            config_id=text(row, "config_id"),
            url=text(row, "url"),
            authentication_scheme=optional_text(row, "authentication_scheme"),
            secret_ref=(
                None
                if token is None
                else ProtectedPayloadRef(token, cast(str, digest))
            ),
            event_id=text(row, "event_id"),
            protocol_version=text(row, "protocol_version"),
            status_sequence=integer(row, "status_sequence"),
            task_state=A2ATaskState(text(row, "task_state")),
            delivered_at=optional_datetime(row, "delivered_at"),
            suppressed_at=optional_datetime(row, "suppressed_at"),
            abandoned_at=optional_datetime(row, "abandoned_at"),
        )
    except (KeyError, TypeError, ValueError):
        raise DeliveryUnavailableError() from None


def is_terminal_delivery(row: Row) -> bool:
    return any(
        row[field_name] is not None
        for field_name in ("delivered_at", "suppressed_at", "abandoned_at")
    )


def is_current_attempt(row: Row | None, attempt_id: str) -> bool:
    if row is None or is_terminal_delivery(row) or row["attempt_id"] != attempt_id:
        return False
    expires_at = optional_datetime(row, "attempt_expires_at")
    return expires_at is not None and expires_at > datetime_value(row, "database_now")


def text(row: Row, field_name: str) -> str:
    value = row[field_name]
    if type(value) is not str:
        raise TypeError(f"{field_name} must be str")
    return value


def optional_text(row: Row, field_name: str) -> str | None:
    value = row[field_name]
    if value is not None and type(value) is not str:
        raise TypeError(f"{field_name} must be str or None")
    return cast(str | None, value)


def integer(row: Row, field_name: str) -> int:
    value = row[field_name]
    if type(value) is not int:
        raise TypeError(f"{field_name} must be int")
    return value


def datetime_value(row: Row, field_name: str) -> datetime:
    value = row[field_name]
    if type(value) is not datetime or value.utcoffset() is None:
        raise TypeError(f"{field_name} must be timezone-aware datetime")
    return value.astimezone(UTC)


def optional_datetime(row: Row, field_name: str) -> datetime | None:
    value = row[field_name]
    return None if value is None else datetime_value(row, field_name)


__all__ = [
    "datetime_value",
    "integer",
    "is_current_attempt",
    "is_terminal_delivery",
    "optional_datetime",
    "optional_text",
    "push_delivery_target_from_row",
    "text",
]
