from __future__ import annotations

import json
from datetime import UTC, datetime

from agentos.multi.team_delivery_types import TeamMessageReceipt
from agentos.multi.team_errors import TeamToolResultTooLargeError
from agentos.multi.team_types import (
    MAX_TEAM_MEMBER_CAPABILITIES,
    TEAM_MESSAGE_PAGE_LIMIT,
    TeamMemberRecord,
    TeamMessage,
    TeamMessagePage,
    TeamRecord,
)


_MAX_TEAM_TOOL_RESULT_BYTES = 64 * 1024


def team_create_schema() -> dict[str, object]:
    """返回 team_create 的严格参数 schema。"""

    return _object_schema(
        {"team_id": _identifier_schema()},
        required=("team_id",),
    )


def agent_create_schema() -> dict[str, object]:
    """返回 agent_create 的严格参数 schema。"""

    return _object_schema(
        {
            "team_id": _identifier_schema(),
            "recipient_agent_id": _identifier_schema(),
            "target_session_id": _identifier_schema(),
            "capabilities": {
                "type": "array",
                "items": _identifier_schema(),
                "uniqueItems": True,
                "maxItems": MAX_TEAM_MEMBER_CAPABILITIES,
            },
        },
        required=("team_id", "recipient_agent_id", "target_session_id"),
    )


def team_say_schema() -> dict[str, object]:
    """返回 team_say 的严格参数 schema。"""

    return _object_schema(
        {
            "team_id": _identifier_schema(),
            "content": {"type": "string", "minLength": 1},
            "addressing_kind": {"type": "string", "enum": ["direct", "broadcast"]},
            "addressed_agent_id": _identifier_schema(),
            "message_kind": {
                "type": "string",
                "enum": ["instruction", "observation", "result", "notice"],
            },
            "correlation_id": _identifier_schema(),
        },
        required=("team_id", "content", "addressing_kind"),
    )


def team_read_messages_schema() -> dict[str, object]:
    """返回 team_read_messages 的严格参数 schema。"""

    return _object_schema(
        {
            "team_id": _identifier_schema(),
            "after_message_id": _identifier_schema(),
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": TEAM_MESSAGE_PAGE_LIMIT,
            },
        },
        required=("team_id",),
    )


def team_delete_schema() -> dict[str, object]:
    """返回 team_delete 的严格参数 schema。"""

    return _object_schema(
        {"team_id": _identifier_schema()},
        required=("team_id",),
    )


def render_team(team: TeamRecord) -> str:
    """渲染不暴露内部 authority 的 Team Tool Result。"""

    return _json_dump(
        {
            "team_id": team.team_id,
            "leader_agent_id": team.leader_agent_id,
            "workspace_id": (
                None if team.workspace is None else team.workspace.workspace_id
            ),
            "created_at": _utc_json(team.created_at),
            "status": team.status,
            "deleted_at": _optional_utc_json(team.deleted_at),
        },
    )


def render_member(member: TeamMemberRecord) -> str:
    """渲染不包含目标 Session binding 的成员 Tool Result。"""

    return _json_dump(
        {
            "team_id": member.team_id,
            "recipient_agent_id": member.recipient_agent_id,
            "role": member.role,
            "capabilities": list(member.capabilities),
            "created_at": _utc_json(member.created_at),
            "status": member.status,
            "deleted_at": _optional_utc_json(member.deleted_at),
        },
    )


def render_receipt(receipt: TeamMessageReceipt) -> str:
    """渲染只包含 public message fields 的发送回执。"""

    return _json_dump(
        {
            "message": _message_json(receipt.message),
            "accepted_recipient_count": len(receipt.deliveries),
            "duplicate": receipt.duplicate,
        },
    )


def render_message_page(page: TeamMessagePage) -> str:
    """渲染有界 Team message page。"""

    return _json_dump(
        {
            "messages": [_message_json(message) for message in page.messages],
            "next_cursor": page.next_cursor,
        },
    )


def _object_schema(
    properties: dict[str, object],
    *,
    required: tuple[str, ...],
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _identifier_schema() -> dict[str, object]:
    return {"type": "string", "minLength": 1, "maxLength": 255}


def _message_json(message: TeamMessage) -> dict[str, object]:
    return {
        "message_id": message.message_id,
        "team_id": message.team_id,
        "sender_agent_id": message.sender_agent_id,
        "message_kind": message.message_kind,
        "content": message.content,
        "correlation_id": message.correlation_id,
        "addressing_kind": message.addressing_kind,
        "addressed_agent_id": message.addressed_agent_id,
        "created_at": _utc_json(message.created_at),
    }


def _utc_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _optional_utc_json(value: datetime | None) -> str | None:
    return None if value is None else _utc_json(value)


def _json_dump(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(rendered.encode("utf-8")) > _MAX_TEAM_TOOL_RESULT_BYTES:
        raise TeamToolResultTooLargeError
    return rendered


__all__ = [
    "agent_create_schema",
    "render_member",
    "render_message_page",
    "render_receipt",
    "render_team",
    "team_create_schema",
    "team_delete_schema",
    "team_read_messages_schema",
    "team_say_schema",
]
