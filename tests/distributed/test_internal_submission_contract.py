from __future__ import annotations

from agentos._json_values import FrozenJsonObject
from agentos.distributed.internal_models import (
    InternalRunInputReceipt,
    InternalRunSubmission,
    InternalSubmissionAuthority,
)
from agentos.distributed.internal_services import InternalRunSubmissionService
from agentos.distributed.models import RequestScope, RunSubmissionReceipt
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "team_delivery_service")


def _submission() -> InternalRunSubmission:
    return InternalRunSubmission(
        session_id="session_2",
        submission_id="team_submission_1",
        source_kind="team_message",
        source_payload={
            "team_id": "team_1",
            "message_id": "team_msg_1",
            "recipient_agent_id": "agent_2",
            "action": "team_read_messages",
        },
    )


def test_internal_submission_models_are_frozen_and_strict() -> None:
    submission = _submission()
    authority = InternalSubmissionAuthority(
        delivery_id="team_delivery_1",
        claim_id="claim_1",
        fence=2,
    )

    assert type(submission.source_payload) is FrozenJsonObject
    assert authority.fence == 2


class FakeInternalSubmissionPort:
    def __init__(self) -> None:
        self.calls: list[tuple[RequestScope, InternalRunSubmission, InternalSubmissionAuthority]] = []
        self.wakeup_calls: list[
            tuple[RequestScope, str, DurableRunCommand, InternalSubmissionAuthority]
        ] = []
        self.applied: InternalRunInputReceipt | None = None

    async def submit_internal(
        self,
        *,
        scope: RequestScope,
        submission: InternalRunSubmission,
        authority: InternalSubmissionAuthority,
    ) -> RunSubmissionReceipt:
        self.calls.append((scope, submission, authority))
        return RunSubmissionReceipt(
            submission.session_id,
            "run_1",
            submission.submission_id,
            1,
            False,
        )

    async def submit_wakeup(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
        authority: InternalSubmissionAuthority,
    ) -> DurableCommandReceipt:
        self.wakeup_calls.append((scope, session_id, command, authority))
        return DurableCommandReceipt(
            command.run_id,
            command.command_id,
            command.kind,
            4,
            False,
        )

    async def get_applied_input(
        self,
        *,
        scope: RequestScope,
        authority: InternalSubmissionAuthority,
    ) -> InternalRunInputReceipt | None:
        return self.applied


@async_test
async def test_internal_submission_service_forwards_typed_authority() -> None:
    port = FakeInternalSubmissionPort()
    service = InternalRunSubmissionService(port)
    submission = _submission()
    authority = InternalSubmissionAuthority("team_delivery_1", "claim_1", 2)

    receipt = await service.submit(SCOPE, submission, authority)

    assert receipt == RunSubmissionReceipt(
        "session_2",
        "run_1",
        "team_submission_1",
        1,
        False,
    )
    assert port.calls == [(SCOPE, submission, authority)]


@async_test
async def test_internal_submission_service_forwards_team_wakeup_authority() -> None:
    port = FakeInternalSubmissionPort()
    service = InternalRunSubmissionService(port)
    authority = InternalSubmissionAuthority("team_delivery_1", "claim_1", 2)
    command = DurableRunCommand(
        "run_1",
        "team_command_1",
        "wakeup",
        _submission().source_payload,
    )

    receipt = await service.submit_wakeup(SCOPE, "session_2", command, authority)

    assert receipt == DurableCommandReceipt("run_1", "team_command_1", "wakeup", 4, False)
    assert port.wakeup_calls == [(SCOPE, "session_2", command, authority)]


@async_test
async def test_internal_submission_service_recovers_applied_input() -> None:
    port = FakeInternalSubmissionPort()
    service = InternalRunSubmissionService(port)
    authority = InternalSubmissionAuthority("team_delivery_1", "claim_1", 2)
    port.applied = InternalRunInputReceipt(
        "team_delivery_1",
        "session_2",
        "wakeup",
        "run_1",
        4,
    )

    assert await service.get_applied_input(SCOPE, authority) == port.applied
