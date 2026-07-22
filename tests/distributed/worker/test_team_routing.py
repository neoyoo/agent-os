from __future__ import annotations

from dataclasses import replace

import pytest

from agentos._waiting import WaitReason
from agentos.distributed.errors import ActiveRunConflictError, CommandStateError
from agentos.distributed.internal_errors import (
    InternalSubmissionBindingRevokedError,
    StaleInternalSubmissionAuthorityError,
)
from agentos.distributed.internal_models import InternalRunInputReceipt
from agentos.distributed.models import (
    QueueDelivery,
    RequestScope,
    RunReadModel,
    RunSubmissionReceipt,
)
from agentos.multi.team_delivery_types import TeamDeliveryResult
from agentos.multi.team_errors import TeamConflictError
from agentos.multi.team_identity import team_command_id, team_submission_id
from agentos.runtime.durable_commands import DurableCommandReceipt
from agentos.runtime.run_state import RunStatus
from tests.distributed.worker.test_team_runner import (
    FakeBootstrap,
    FakeDeliveries,
    FakeQueue,
    FakeTeams,
    _active_member,
    _claimed,
    _runner,
    _target,
)
from tests.planning._async import async_test


class FakeRunQueries:
    def __init__(self, values: list[RunReadModel | None]) -> None:
        self.values = values
        self.calls: list[tuple[RequestScope, str]] = []

    async def get_active(
        self,
        scope: RequestScope,
        session_id: str,
    ) -> RunReadModel | None:
        self.calls.append((scope, session_id))
        return self.values.pop(0)


class FakeInternalSubmissions:
    def __init__(
        self,
        outcomes: list[RunSubmissionReceipt | BaseException],
        *,
        wakeup_outcomes: list[DurableCommandReceipt | BaseException] | None = None,
        applied: InternalRunInputReceipt | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.wakeup_outcomes = wakeup_outcomes or []
        self.applied = applied
        self.calls: list[tuple[RequestScope, object, object]] = []
        self.wakeup_calls: list[tuple[RequestScope, str, object, object]] = []
        self.applied_calls: list[tuple[RequestScope, object]] = []

    async def submit(
        self,
        scope: RequestScope,
        submission: object,
        authority: object,
    ) -> RunSubmissionReceipt:
        self.calls.append((scope, submission, authority))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def submit_wakeup(
        self,
        scope: RequestScope,
        session_id: str,
        command: object,
        authority: object,
    ) -> DurableCommandReceipt:
        self.wakeup_calls.append((scope, session_id, command, authority))
        outcome = self.wakeup_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def get_applied_input(
        self,
        scope: RequestScope,
        authority: object,
    ) -> InternalRunInputReceipt | None:
        self.applied_calls.append((scope, authority))
        return self.applied


def _run(
    *,
    status: RunStatus,
    aggregate_version: int = 3,
    wait_reason: WaitReason | None = None,
) -> RunReadModel:
    return RunReadModel(
        tenant_id="tenant_1",
        session_id="session_2",
        run_id="run_1",
        status=status,
        wait_reason=wait_reason,
        aggregate_version=aggregate_version,
        result=None,
    )


def _claimed_runner(
    *,
    target=None,
    queries: FakeRunQueries,
    internal: FakeInternalSubmissions | None = None,
    teams: FakeTeams | None = None,
    trace: list[str] | None = None,
):
    selected_target = _target() if target is None else target
    claimed = _claimed(selected_target)
    selected_trace = [] if trace is None else trace
    deliveries = FakeDeliveries(claimed, trace=selected_trace)
    queue = FakeQueue(selected_trace)
    runner, _, _ = _runner(
        bootstrap=FakeBootstrap([selected_target]),
        deliveries=deliveries,
        queue=queue,
        teams=teams or FakeTeams(_active_member()),
        run_queries=queries,
        internal_submissions=internal or FakeInternalSubmissions([]),
    )
    delivery = QueueDelivery("1-0", selected_target.outbox_id, 1)
    return runner, delivery, deliveries, queue


@async_test
async def test_revoked_binding_is_durably_rejected_before_run_services() -> None:
    queries = FakeRunQueries([])
    internal = FakeInternalSubmissions([])
    runner, delivery, deliveries, queue = _claimed_runner(
        queries=queries,
        internal=internal,
        teams=FakeTeams(None),
    )

    assert await runner.run_delivery(delivery) is True

    assert deliveries.results == [
        TeamDeliveryResult("rejected_binding_revoked", None, None, None)
    ]
    assert queries.calls == []
    assert internal.calls == []
    assert internal.wakeup_calls == []
    assert len(internal.applied_calls) == 1
    assert queue.acked == [delivery]


@pytest.mark.parametrize(
    "member",
    (
        replace(_active_member(), team_id="other_team"),
        replace(_active_member(), recipient_agent_id="other_agent"),
    ),
)
@async_test
async def test_binding_port_cannot_return_another_resource(member) -> None:
    queries = FakeRunQueries([None])
    internal = FakeInternalSubmissions([])
    runner, delivery, deliveries, queue = _claimed_runner(
        queries=queries,
        internal=internal,
        teams=FakeTeams(member),
    )

    with pytest.raises(RuntimeError, match="binding"):
        await runner.run_delivery(delivery)

    assert queries.calls == []
    assert internal.calls == []
    assert deliveries.results == []
    assert queue.acked == []


@async_test
async def test_no_active_run_uses_deterministic_internal_submission() -> None:
    target = _target()
    submission_id = team_submission_id(
        scope=RequestScope("tenant_1", "team_delivery_service"),
        delivery_id=target.delivery.delivery_id,
        target_session_id="session_2",
    )
    receipt = RunSubmissionReceipt("session_2", "run_2", submission_id, 1, False)
    queries = FakeRunQueries([None])
    internal = FakeInternalSubmissions([receipt])
    runner, delivery, deliveries, queue = _claimed_runner(
        target=target,
        queries=queries,
        internal=internal,
    )

    assert await runner.run_delivery(delivery) is True

    scope, submission, authority = internal.calls[0]
    assert scope == RequestScope("tenant_1", "team_delivery_service")
    assert type(submission).__name__ == "InternalRunSubmission"
    assert submission.session_id == "session_2"
    assert submission.submission_id == submission_id
    assert submission.source_kind == "team_message"
    assert dict(submission.source_payload) == {
        "team_id": "team_1",
        "message_id": target.message.message_id,
        "recipient_agent_id": "agent_2",
        "action": "team_read_messages",
    }
    assert type(authority).__name__ == "InternalSubmissionAuthority"
    assert (
        authority.delivery_id,
        authority.claim_id,
        authority.fence,
    ) == (
        target.delivery.delivery_id,
        deliveries.claimed.claim.claim_id,
        1,
    )
    assert deliveries.results == [
        TeamDeliveryResult("internal_start", "run_2", 1, RunStatus.QUEUED)
    ]
    assert queue.acked == [delivery]


@async_test
async def test_binding_revoked_during_internal_acceptance_is_durably_rejected() -> None:
    internal = FakeInternalSubmissions([InternalSubmissionBindingRevokedError()])
    runner, delivery, deliveries, queue = _claimed_runner(
        queries=FakeRunQueries([None]),
        internal=internal,
    )

    assert await runner.run_delivery(delivery) is True

    assert len(internal.calls) == 1
    assert deliveries.results == [
        TeamDeliveryResult("rejected_binding_revoked", None, None, None)
    ]
    assert queue.acked == [delivery]


@async_test
async def test_stale_internal_authority_is_not_misreported_as_binding_revoked() -> None:
    internal = FakeInternalSubmissions([StaleInternalSubmissionAuthorityError()])
    runner, delivery, deliveries, queue = _claimed_runner(
        queries=FakeRunQueries([None]),
        internal=internal,
    )

    with pytest.raises(StaleInternalSubmissionAuthorityError):
        await runner.run_delivery(delivery)

    assert deliveries.results == []
    assert queue.acked == []


@pytest.mark.parametrize("input_kind", ["internal_start", "wakeup"])
@async_test
async def test_prior_input_is_restored_before_deleted_binding_and_run_query(
    input_kind: str,
) -> None:
    target = _target()
    internal = FakeInternalSubmissions(
        [],
        applied=InternalRunInputReceipt(
            target.delivery.delivery_id,
            "session_2",
            input_kind,  # type: ignore[arg-type]
            "run_terminal",
            7,
        ),
    )
    queries = FakeRunQueries([])
    teams = FakeTeams(None)
    runner, delivery, deliveries, queue = _claimed_runner(
        target=target,
        queries=queries,
        internal=internal,
        teams=teams,
    )

    assert await runner.run_delivery(delivery) is True

    assert teams.calls == []
    assert queries.calls == []
    assert internal.calls == []
    assert internal.wakeup_calls == []
    assert deliveries.results == [
        TeamDeliveryResult(
            input_kind,  # type: ignore[arg-type]
            "run_terminal",
            7,
            RunStatus.QUEUED,
        )
    ]
    assert queue.acked == [delivery]


@async_test
async def test_applied_input_port_cannot_return_another_session() -> None:
    target = _target()
    internal = FakeInternalSubmissions(
        [],
        applied=InternalRunInputReceipt(
            target.delivery.delivery_id,
            "session_other",
            "wakeup",
            "run_other",
            7,
        ),
    )
    runner, delivery, deliveries, queue = _claimed_runner(
        target=target,
        queries=FakeRunQueries([]),
        internal=internal,
    )

    with pytest.raises(RuntimeError, match="another session"):
        await runner.run_delivery(delivery)

    assert deliveries.results == []
    assert queue.acked == []


@async_test
async def test_matching_remote_wait_uses_deterministic_wakeup_command() -> None:
    target = _target(correlation_id="remote_1")
    active = _run(
        status=RunStatus.WAITING,
        wait_reason=WaitReason("remote_result", "remote_1"),
    )
    command_id = team_command_id(
        scope=RequestScope("tenant_1", "team_delivery_service"),
        delivery_id=target.delivery.delivery_id,
        target_session_id="session_2",
        run_id="run_1",
    )
    command_receipt = DurableCommandReceipt(
        "run_1",
        command_id,
        "wakeup",
        4,
        False,
    )
    queries = FakeRunQueries([active])
    internal = FakeInternalSubmissions([], wakeup_outcomes=[command_receipt])
    runner, delivery, deliveries, queue = _claimed_runner(
        target=target,
        queries=queries,
        internal=internal,
    )

    assert await runner.run_delivery(delivery) is True

    scope, session_id, command, authority = internal.wakeup_calls[0]
    assert scope == RequestScope("tenant_1", "team_delivery_service")
    assert session_id == "session_2"
    assert command.run_id == "run_1"
    assert command.command_id == command_id
    assert command.kind == "wakeup"
    assert dict(command.payload) == {
        "team_id": "team_1",
        "message_id": target.message.message_id,
        "recipient_agent_id": "agent_2",
        "action": "team_read_messages",
    }
    assert authority.delivery_id == target.delivery.delivery_id
    assert internal.calls == []
    assert deliveries.results == [
        TeamDeliveryResult("wakeup", "run_1", 4, RunStatus.QUEUED)
    ]
    assert queue.acked == [delivery]


@async_test
async def test_unmatched_wait_is_durably_rejected_with_observed_evidence() -> None:
    target = _target(correlation_id="message_correlation")
    active = _run(
        status=RunStatus.WAITING,
        wait_reason=WaitReason("remote_result", "different_handle"),
    )
    queries = FakeRunQueries([active])
    internal = FakeInternalSubmissions([])
    runner, delivery, deliveries, queue = _claimed_runner(
        target=target,
        queries=queries,
        internal=internal,
    )

    assert await runner.run_delivery(delivery) is True

    assert deliveries.results == [
        TeamDeliveryResult(
            "rejected_nonterminal",
            "run_1",
            3,
            RunStatus.WAITING,
        )
    ]
    assert internal.calls == []
    assert internal.wakeup_calls == []
    assert queue.acked == [delivery]


@async_test
async def test_first_route_conflict_reloads_once_and_uses_new_route() -> None:
    target = _target(correlation_id="remote_1")
    submission_id = team_submission_id(
        scope=RequestScope("tenant_1", "team_delivery_service"),
        delivery_id=target.delivery.delivery_id,
        target_session_id="session_2",
    )
    active = _run(
        status=RunStatus.WAITING,
        aggregate_version=7,
        wait_reason=WaitReason("resource_availability", "remote_1"),
    )
    command_id = team_command_id(
        scope=RequestScope("tenant_1", "team_delivery_service"),
        delivery_id=target.delivery.delivery_id,
        target_session_id="session_2",
        run_id="run_1",
    )
    queries = FakeRunQueries([None, active])
    internal = FakeInternalSubmissions(
        [ActiveRunConflictError()],
        wakeup_outcomes=[
            DurableCommandReceipt("run_1", command_id, "wakeup", 8, False)
        ],
    )
    runner, delivery, deliveries, queue = _claimed_runner(
        target=target,
        queries=queries,
        internal=internal,
    )

    assert await runner.run_delivery(delivery) is True

    assert internal.calls[0][1].submission_id == submission_id
    assert len(queries.calls) == 2
    assert len(internal.wakeup_calls) == 1
    assert deliveries.results[0].result_kind == "wakeup"
    assert queue.acked == [delivery]


@async_test
async def test_second_route_conflict_releases_claim_without_ack() -> None:
    queries = FakeRunQueries([None, None])
    internal = FakeInternalSubmissions(
        [ActiveRunConflictError(), ActiveRunConflictError()]
    )
    runner, delivery, deliveries, queue = _claimed_runner(
        queries=queries,
        internal=internal,
    )

    assert await runner.run_delivery(delivery) is False

    assert len(queries.calls) == 2
    assert len(internal.calls) == 2
    assert deliveries.release_calls == [deliveries.claimed.claim]
    assert deliveries.results == []
    assert queue.acked == []


@async_test
async def test_second_wakeup_race_releases_claim_without_ack() -> None:
    target = _target(correlation_id="remote_1")
    active = _run(
        status=RunStatus.WAITING,
        wait_reason=WaitReason("remote_result", "remote_1"),
    )
    queries = FakeRunQueries([active, active])
    internal = FakeInternalSubmissions(
        [],
        wakeup_outcomes=[CommandStateError(), CommandStateError()],
    )
    runner, delivery, deliveries, queue = _claimed_runner(
        target=target,
        queries=queries,
        internal=internal,
    )

    assert await runner.run_delivery(delivery) is False

    assert len(internal.wakeup_calls) == 2
    assert deliveries.release_calls == [deliveries.claimed.claim]
    assert queue.acked == []


@async_test
async def test_observed_reject_race_releases_claim_without_ack() -> None:
    active = _run(status=RunStatus.RUNNING)
    queries = FakeRunQueries([active])
    runner, delivery, deliveries, queue = _claimed_runner(queries=queries)
    deliveries.commit_error = TeamConflictError()

    assert await runner.run_delivery(delivery) is False

    assert deliveries.release_calls == [deliveries.claimed.claim]
    assert queue.acked == []
