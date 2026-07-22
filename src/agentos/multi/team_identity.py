from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope
from agentos.multi.team_types import TeamAddressingKind, TeamMessageKind


_MESSAGE_DOMAIN = "agentos.team.message.v1"
_DELIVERY_DOMAIN = "agentos.team.delivery.v1"
_SUBMISSION_DOMAIN = "agentos.team.submission.v1"
_COMMAND_DOMAIN = "agentos.team.command.v1"
_OUTBOX_DOMAIN = "agentos.team.outbox.v1"
_MESSAGE_KINDS = frozenset({"instruction", "observation", "result", "notice"})
_ADDRESSING_KINDS = frozenset({"direct", "broadcast"})
_OUTBOX_KINDS = frozenset({"delivery_ready", "delivery_result"})


def team_message_id(
    *,
    scope: RequestScope,
    team_id: str,
    sender_agent_id: str,
    operation_id: str,
) -> str:
    """生成 canonical Team message identity。"""

    _require_scope(scope)
    _require_ids(
        team_id=team_id,
        sender_agent_id=sender_agent_id,
        operation_id=operation_id,
    )
    return _identity(
        _MESSAGE_DOMAIN,
        "team_msg_",
        {
            "version": 1,
            "tenant_id": scope.tenant_id,
            "team_id": team_id,
            "sender_agent_id": sender_agent_id,
            "operation_id": operation_id,
        },
    )


def team_delivery_id(
    *,
    scope: RequestScope,
    team_id: str,
    message_id: str,
    recipient_agent_id: str,
    target_session_id: str,
) -> str:
    """生成 canonical per-recipient delivery identity。"""

    _require_scope(scope)
    _require_ids(
        team_id=team_id,
        message_id=message_id,
        recipient_agent_id=recipient_agent_id,
        target_session_id=target_session_id,
    )
    return _identity(
        _DELIVERY_DOMAIN,
        "team_delivery_",
        {
            "version": 1,
            "tenant_id": scope.tenant_id,
            "team_id": team_id,
            "message_id": message_id,
            "recipient_agent_id": recipient_agent_id,
            "target_session_id": target_session_id,
        },
    )


def team_submission_id(
    *,
    scope: RequestScope,
    delivery_id: str,
    target_session_id: str,
) -> str:
    """生成 Team internal-start submission identity。"""

    _require_scope(scope)
    _require_ids(delivery_id=delivery_id, target_session_id=target_session_id)
    return _identity(
        _SUBMISSION_DOMAIN,
        "team_submission_",
        {
            "version": 1,
            "tenant_id": scope.tenant_id,
            "delivery_id": delivery_id,
            "target_session_id": target_session_id,
        },
    )


def team_command_id(
    *,
    scope: RequestScope,
    delivery_id: str,
    target_session_id: str,
    run_id: str,
) -> str:
    """生成 Team wakeup command identity。"""

    _require_scope(scope)
    _require_ids(
        delivery_id=delivery_id,
        target_session_id=target_session_id,
        run_id=run_id,
    )
    return _identity(
        _COMMAND_DOMAIN,
        "team_command_",
        {
            "version": 1,
            "tenant_id": scope.tenant_id,
            "delivery_id": delivery_id,
            "target_session_id": target_session_id,
            "run_id": run_id,
            "command_kind": "wakeup",
        },
    )


def team_outbox_id(
    *,
    scope: RequestScope,
    delivery_id: str,
    outbox_kind: str,
) -> str:
    """生成 Team delivery ready/result outbox identity。"""

    _require_scope(scope)
    _require_ids(delivery_id=delivery_id)
    if outbox_kind not in _OUTBOX_KINDS:
        raise ValueError("outbox_kind is invalid")
    return _identity(
        _OUTBOX_DOMAIN,
        "team_outbox_",
        {
            "version": 1,
            "tenant_id": scope.tenant_id,
            "delivery_id": delivery_id,
            "outbox_kind": outbox_kind,
        },
    )


def team_message_request_digest(
    *,
    scope: RequestScope,
    team_id: str,
    message_id: str,
    sender_agent_id: str,
    message_kind: TeamMessageKind,
    content: str,
    correlation_id: str | None,
    addressing_kind: TeamAddressingKind,
    addressed_agent_id: str | None,
) -> str:
    """计算 Team message 完整请求语义的 canonical digest。"""

    payload = _message_digest_payload(
        scope=scope,
        team_id=team_id,
        message_id=message_id,
        sender_agent_id=sender_agent_id,
        message_kind=message_kind,
        content=content,
        correlation_id=correlation_id,
        addressing_kind=addressing_kind,
        addressed_agent_id=addressed_agent_id,
    )
    return sha256(_canonical_bytes(payload)).hexdigest()


def team_delivery_source_digest(
    *,
    scope: RequestScope,
    team_id: str,
    message_id: str,
    sender_agent_id: str,
    message_kind: TeamMessageKind,
    content: str,
    correlation_id: str | None,
    addressing_kind: TeamAddressingKind,
    addressed_agent_id: str | None,
    recipient_agent_id: str,
    target_session_id: str,
) -> str:
    """计算一条 per-recipient delivery source 的 canonical digest。"""

    payload = _message_digest_payload(
        scope=scope,
        team_id=team_id,
        message_id=message_id,
        sender_agent_id=sender_agent_id,
        message_kind=message_kind,
        content=content,
        correlation_id=correlation_id,
        addressing_kind=addressing_kind,
        addressed_agent_id=addressed_agent_id,
    )
    _require_ids(
        recipient_agent_id=recipient_agent_id,
        target_session_id=target_session_id,
    )
    payload.update(
        {
            "recipient_agent_id": recipient_agent_id,
            "target_session_id": target_session_id,
        },
    )
    return sha256(_canonical_bytes(payload)).hexdigest()


def _message_digest_payload(
    *,
    scope: RequestScope,
    team_id: str,
    message_id: str,
    sender_agent_id: str,
    message_kind: str,
    content: object,
    correlation_id: str | None,
    addressing_kind: str,
    addressed_agent_id: str | None,
) -> dict[str, object]:
    _require_scope(scope)
    _require_ids(
        team_id=team_id,
        message_id=message_id,
        sender_agent_id=sender_agent_id,
    )
    if message_kind not in _MESSAGE_KINDS:
        raise ValueError("message_kind is invalid")
    if type(content) is not str or not content.strip():
        raise ValueError("content must be a non-empty string")
    if correlation_id is not None:
        require_identifier(correlation_id, "correlation_id")
    if addressing_kind not in _ADDRESSING_KINDS:
        raise ValueError("addressing_kind is invalid")
    if addressing_kind == "direct":
        if addressed_agent_id is None:
            raise ValueError("direct addressing requires addressed_agent_id")
        require_identifier(addressed_agent_id, "addressed_agent_id")
    elif addressed_agent_id is not None:
        raise ValueError("broadcast addressing cannot contain addressed_agent_id")
    return {
        "version": 1,
        "tenant_id": scope.tenant_id,
        "team_id": team_id,
        "message_id": message_id,
        "sender_agent_id": sender_agent_id,
        "message_kind": message_kind,
        "content": content,
        "correlation_id": correlation_id,
        "addressing_kind": addressing_kind,
        "addressed_agent_id": addressed_agent_id,
    }


def _identity(
    domain_label: str,
    prefix: str,
    value: Mapping[str, object],
) -> str:
    digest = sha256(
        domain_label.encode("ascii") + b"\x00" + _canonical_bytes(value),
    ).hexdigest()
    return prefix + digest


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


def _require_ids(**values: object) -> None:
    for field_name, value in values.items():
        require_identifier(value, field_name)
