from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentos.multi.team_delivery_types import (
    ClaimedTeamDelivery,
    TeamDelivery,
    TeamDeliveryClaim,
    TeamDeliveryResult,
    TeamDeliveryTarget,
    TeamMessageReceipt,
)
from agentos.multi.team_event_types import (
    TeamDeliveryAppliedEvent,
    TeamDeliveryRejectedEvent,
    TeamEventEnvelope,
    TeamEventReplayBatch,
    TeamEventReplayItem,
    TeamEventTarget,
    TeamStreamGap,
)
from agentos.multi.team_types import (
    MAX_TEAM_MEMBER_CAPABILITIES,
    MAX_TEAM_MESSAGE_CONTENT_BYTES,
    TeamMemberRecord,
    TeamMessage,
    TeamMessagePage,
    TeamMessageRequest,
    TeamRecipient,
    TeamRecord,
)
from agentos.runtime.run_state import RunStatus
from agentos.workspace import WorkspaceHandle


NOW = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)


def _team() -> TeamRecord:
    return TeamRecord(
        team_id="team_1",
        leader_agent_id="agent_1",
        workspace=WorkspaceHandle(
            workspace_id="workspace_1",
            scope="session",
            root="/workspace/team_1",
        ),
        created_at=NOW,
    )


def _member() -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_2",
        role="worker",
        target_session_id="session_2",
        capabilities=("read",),
        created_at=NOW,
    )


def _request() -> TeamMessageRequest:
    return TeamMessageRequest(
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="instruction",
        content="inspect drawing",
        correlation_id="wait_1",
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        created_at=NOW,
    )


def _message() -> TeamMessage:
    return TeamMessage(
        message_id="team_msg_" + "1" * 64,
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="instruction",
        content="inspect drawing",
        correlation_id="wait_1",
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        recipient_snapshot=(TeamRecipient("agent_2", "session_2"),),
        request_sha256="2" * 64,
        created_at=NOW,
    )


def _delivery() -> TeamDelivery:
    return TeamDelivery(
        delivery_id="team_delivery_" + "3" * 64,
        team_id="team_1",
        message_id="team_msg_" + "1" * 64,
        recipient_agent_id="agent_2",
        target_session_id="session_2",
        state="pending",
        fencing_token=0,
        source_sha256="4" * 64,
        created_at=NOW,
        updated_at=NOW,
    )


def test_team_domain_values_normalize_datetime_and_freeze_containers() -> None:
    offset = timezone(timedelta(hours=8))
    member = TeamMemberRecord(
        team_id="team_1",
        recipient_agent_id="agent_2",
        role="worker",
        target_session_id="session_2",
        capabilities=["write", "read"],
        created_at=NOW.astimezone(offset),
    )

    assert member.created_at == NOW
    assert member.created_at.tzinfo is UTC
    assert member.capabilities == ("read", "write")
    assert _team().workspace is not None


def test_team_member_capabilities_reject_duplicates_and_raw_overflow() -> None:
    common = {
        "team_id": "team_1",
        "recipient_agent_id": "agent_2",
        "role": "worker",
        "target_session_id": "session_2",
        "created_at": NOW,
    }

    with pytest.raises(ValueError, match="duplicate"):
        TeamMemberRecord(capabilities=("read", "read"), **common)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="more than 32"):
        TeamMemberRecord(
            capabilities=("read",) * (MAX_TEAM_MEMBER_CAPABILITIES + 1),
            **common,  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError, match="capabilities must contain identifier values"):
        TeamMemberRecord(
            capabilities=(item for item in ("read", "write")),
            **common,  # type: ignore[arg-type]
        )


def test_team_record_workspace_is_transitively_immutable() -> None:
    metadata = {"session_id": "session_1"}
    workspace = WorkspaceHandle(
        workspace_id="workspace_1",
        scope="session",
        metadata=metadata,
    )
    team = TeamRecord(
        team_id="team_1",
        leader_agent_id="agent_1",
        workspace=workspace,
        created_at=NOW,
    )

    metadata["session_id"] = "session_tampered"

    assert team.workspace is not None
    assert team.workspace.metadata == {"session_id": "session_1"}
    with pytest.raises(TypeError):
        team.workspace.metadata["session_id"] = "session_other"  # type: ignore[index]


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: TeamRecord(
                team_id="team_1",
                leader_agent_id="agent_1",
                workspace=None,
                created_at=NOW.replace(tzinfo=None),
            ),
            "timezone-aware",
        ),
        (
            lambda: TeamMemberRecord(
                team_id="team_1",
                recipient_agent_id="agent_2",
                role="worker",
                target_session_id="",
                created_at=NOW,
            ),
            "target_session_id",
        ),
        (
            lambda: TeamRecord(
                team_id="team_1",
                leader_agent_id="agent_1",
                workspace=None,
                created_at=NOW,
                status="deleted",
            ),
            "deleted_at",
        ),
        (
            lambda: TeamMemberRecord(
                team_id="team_1",
                recipient_agent_id="agent_2",
                role="observer",
                target_session_id="session_2",
                created_at=NOW,
            ),
            "role",
        ),
    ],
)
def test_team_domain_rejects_invalid_state(factory: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        factory()  # type: ignore[operator]


def test_team_message_uses_exact_addressing_and_has_no_artifact_field() -> None:
    assert "artifact_handles" not in {field.name for field in fields(TeamMessage)}

    with pytest.raises(ValueError, match="addressed_agent_id"):
        TeamMessageRequest(
            team_id="team_1",
            sender_agent_id="agent_1",
            operation_id="operation_1",
            message_kind="instruction",
            content="inspect drawing",
            correlation_id=None,
            addressing_kind="broadcast",
            addressed_agent_id="agent_2",
            created_at=NOW,
        )

    with pytest.raises(ValueError, match="recipient_snapshot"):
        TeamMessage(
            message_id="team_msg_" + "1" * 64,
            team_id="team_1",
            sender_agent_id="agent_1",
            operation_id="operation_1",
            message_kind="instruction",
            content="inspect drawing",
            correlation_id=None,
            addressing_kind="broadcast",
            addressed_agent_id=None,
            recipient_snapshot=(),
            request_sha256="2" * 64,
            created_at=NOW,
        )


def test_team_message_recipient_snapshot_is_canonical_and_immutable() -> None:
    message = TeamMessage(
        message_id="team_msg_" + "1" * 64,
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="notice",
        content="status",
        correlation_id=None,
        addressing_kind="broadcast",
        addressed_agent_id=None,
        recipient_snapshot=[
            TeamRecipient("agent_3", "session_3"),
            TeamRecipient("agent_2", "session_2"),
        ],
        request_sha256="2" * 64,
        created_at=NOW,
    )

    assert message.recipient_snapshot == (
        TeamRecipient("agent_2", "session_2"),
        TeamRecipient("agent_3", "session_3"),
    )


def test_team_message_content_uses_utf8_byte_hard_max() -> None:
    accepted = TeamMessageRequest(
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
        message_kind="instruction",
        content="a" * MAX_TEAM_MESSAGE_CONTENT_BYTES,
        correlation_id=None,
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        created_at=NOW,
    )

    assert len(accepted.content.encode("utf-8")) == MAX_TEAM_MESSAGE_CONTENT_BYTES
    with pytest.raises(ValueError, match="4096 UTF-8 bytes"):
        TeamMessageRequest(
            team_id="team_1",
            sender_agent_id="agent_1",
            operation_id="operation_2",
            message_kind="instruction",
            content="你" * 1366,
            correlation_id=None,
            addressing_kind="direct",
            addressed_agent_id="agent_2",
            created_at=NOW,
        )


def test_team_message_page_requires_canonical_next_cursor() -> None:
    message = _message()
    page = TeamMessagePage(messages=(message,), next_cursor=message.message_id)

    assert page.messages == (message,)
    with pytest.raises(ValueError, match="next_cursor"):
        TeamMessagePage(messages=(message,), next_cursor="team_msg_other")


def test_team_delivery_claim_and_result_enforce_fence_shape() -> None:
    delivery = TeamDelivery(
        delivery_id="team_delivery_" + "3" * 64,
        team_id="team_1",
        message_id="team_msg_" + "1" * 64,
        recipient_agent_id="agent_2",
        target_session_id="session_2",
        state="claimed",
        fencing_token=1,
        source_sha256="4" * 64,
        created_at=NOW,
        updated_at=NOW,
        claim_id="claim_1",
        claim_expires_at=NOW + timedelta(seconds=30),
    )
    target = TeamDeliveryTarget(
        tenant_id="tenant_1",
        outbox_id="team_outbox_" + "5" * 64,
        delivery=delivery,
        message=_message(),
    )
    claim = TeamDeliveryClaim(
        tenant_id="tenant_1",
        team_id="team_1",
        delivery_id=delivery.delivery_id,
        claim_id="claim_1",
        fencing_token=1,
        expires_at=NOW + timedelta(seconds=30),
    )

    claimed = ClaimedTeamDelivery(target=target, claim=claim)
    result = TeamDeliveryResult(
        result_kind="wakeup",
        observed_run_id="run_1",
        observed_aggregate_version=2,
        observed_run_status=RunStatus.WAITING,
    )

    assert claimed.claim.fencing_token == 1
    assert result.observed_run_status is RunStatus.WAITING

    with pytest.raises(ValueError, match="fencing_token"):
        TeamDeliveryClaim(
            tenant_id="tenant_1",
            team_id="team_1",
            delivery_id=delivery.delivery_id,
            claim_id="claim_1",
            fencing_token=0,
            expires_at=NOW,
        )


def test_team_message_receipt_rejects_mismatched_delivery_binding() -> None:
    message = _message()
    delivery = TeamDelivery(
        delivery_id="team_delivery_" + "3" * 64,
        team_id="team_other",
        message_id=message.message_id,
        recipient_agent_id="agent_2",
        target_session_id="session_other",
        state="pending",
        fencing_token=0,
        source_sha256="4" * 64,
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ValueError, match="deliveries"):
        TeamMessageReceipt(message=message, deliveries=(delivery,), duplicate=False)


def test_claimed_team_delivery_requires_persisted_claim_identity() -> None:
    message = _message()
    persisted = TeamDelivery(
        delivery_id="team_delivery_" + "3" * 64,
        team_id="team_1",
        message_id=message.message_id,
        recipient_agent_id="agent_2",
        target_session_id="session_2",
        state="claimed",
        fencing_token=2,
        source_sha256="4" * 64,
        created_at=NOW,
        updated_at=NOW,
        claim_id="claim_2",
        claim_expires_at=NOW + timedelta(seconds=30),
    )
    target = TeamDeliveryTarget(
        tenant_id="tenant_1",
        outbox_id="team_outbox_" + "5" * 64,
        delivery=persisted,
        message=message,
    )
    stale_claim = TeamDeliveryClaim(
        tenant_id="tenant_1",
        team_id="team_1",
        delivery_id=persisted.delivery_id,
        claim_id="claim_1",
        fencing_token=1,
        expires_at=NOW + timedelta(seconds=30),
    )

    with pytest.raises(ValueError, match="claim"):
        ClaimedTeamDelivery(target=target, claim=stale_claim)


def test_team_delivery_terminal_state_matches_result_kind() -> None:
    with pytest.raises(ValueError, match="result_kind"):
        TeamDelivery(
            delivery_id="team_delivery_" + "3" * 64,
            team_id="team_1",
            message_id="team_msg_" + "1" * 64,
            recipient_agent_id="agent_2",
            target_session_id="session_2",
            state="applied",
            fencing_token=1,
            source_sha256="4" * 64,
            result_kind="rejected_nonterminal",
            observed_run_id="run_1",
            observed_aggregate_version=2,
            observed_run_status=RunStatus.RUNNING,
            created_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.parametrize(
    "status",
    (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED),
)
def test_rejected_nonterminal_result_rejects_terminal_run_status(
    status: RunStatus,
) -> None:
    with pytest.raises(ValueError, match="nonterminal"):
        TeamDeliveryResult(
            result_kind="rejected_nonterminal",
            observed_run_id="run_1",
            observed_aggregate_version=2,
            observed_run_status=status,
        )


@pytest.mark.parametrize(
    "terminal_fields",
    (
        {
            "state": "applied",
            "result_kind": "wakeup",
            "observed_run_id": "run_1",
            "observed_aggregate_version": 2,
            "observed_run_status": RunStatus.WAITING,
        },
        {
            "state": "rejected",
            "result_kind": "rejected_binding_revoked",
        },
    ),
)
def test_terminal_delivery_requires_positive_fence(
    terminal_fields: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="fencing_token"):
        TeamDelivery(
            delivery_id="team_delivery_" + "3" * 64,
            team_id="team_1",
            message_id="team_msg_" + "1" * 64,
            recipient_agent_id="agent_2",
            target_session_id="session_2",
            fencing_token=0,
            source_sha256="4" * 64,
            created_at=NOW,
            updated_at=NOW,
            **terminal_fields,  # type: ignore[arg-type]
        )


def test_team_event_replay_types_are_team_scoped() -> None:
    event = TeamEventEnvelope(
        tenant_id="tenant_1",
        team_id="team_1",
        event_sequence=1,
        event=TeamDeliveryAppliedEvent(
            delivery_id="team_delivery_" + "3" * 64,
            result=TeamDeliveryResult(
                result_kind="wakeup",
                observed_run_id="run_1",
                observed_aggregate_version=2,
                observed_run_status=RunStatus.WAITING,
            ),
        ),
        occurred_at=NOW,
    )
    item = TeamEventReplayItem(cursor="1-0", event=event)
    batch = TeamEventReplayBatch(items=(item,), next_cursor="1-0")
    gap = TeamStreamGap(
        tenant_id="tenant_1",
        team_id="team_1",
        requested_cursor="0-0",
        oldest_available_cursor="1-0",
        reason="trimmed",
    )

    assert batch.items == (item,)
    assert gap.team_id == "team_1"
    assert event.event_kind == "delivery_applied"


def test_team_delivery_events_enforce_terminal_result_kind() -> None:
    delivery_id = "team_delivery_" + "3" * 64
    applied = TeamDeliveryResult(
        result_kind="wakeup",
        observed_run_id="run_1",
        observed_aggregate_version=2,
        observed_run_status=RunStatus.WAITING,
    )
    rejected = TeamDeliveryResult(
        result_kind="rejected_binding_revoked",
        observed_run_id=None,
        observed_aggregate_version=None,
        observed_run_status=None,
    )

    assert TeamDeliveryAppliedEvent(delivery_id, applied).result is applied
    assert TeamDeliveryRejectedEvent(delivery_id, rejected).result is rejected
    with pytest.raises(ValueError, match="applied"):
        TeamDeliveryAppliedEvent(delivery_id, rejected)
    with pytest.raises(ValueError, match="rejected"):
        TeamDeliveryRejectedEvent(delivery_id, applied)


def test_team_event_target_binds_postgres_tenant_authority() -> None:
    envelope = TeamEventEnvelope(
        tenant_id="tenant_1",
        team_id="team_1",
        event_sequence=1,
        event=TeamDeliveryAppliedEvent(
            delivery_id="team_delivery_" + "3" * 64,
            result=TeamDeliveryResult(
                result_kind="internal_start",
                observed_run_id="run_1",
                observed_aggregate_version=1,
                observed_run_status=RunStatus.QUEUED,
            ),
        ),
        occurred_at=NOW,
    )

    target = TeamEventTarget(
        tenant_id="tenant_1",
        outbox_id="team_outbox_" + "5" * 64,
        event=envelope,
    )

    assert target.event is envelope
    with pytest.raises(ValueError, match="tenant"):
        TeamEventTarget(
            tenant_id="tenant_other",
            outbox_id="team_outbox_" + "5" * 64,
            event=envelope,
        )
