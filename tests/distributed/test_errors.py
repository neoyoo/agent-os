import pytest

from agentos.distributed.errors import (
    ActiveRunConflictError,
    CheckpointConflictError,
    ClaimConflictError,
    ClaimExpiredError,
    CommandConflictError,
    CommandStateError,
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
    RunNotFoundError,
    RunSubmissionConflictError,
    SideEffectAmbiguousError,
    SideEffectInFlightError,
    StaleFenceError,
)


ERRORS = (
    (RunNotFoundError, "run_not_found", "run not found"),
    (RunSubmissionConflictError, "run_submission_conflict", "run submission conflicts with an existing request"),
    (ActiveRunConflictError, "active_run_conflict", "session already has an active run"),
    (CommandConflictError, "command_conflict", "command conflicts with an existing request"),
    (CommandStateError, "command_state", "command is invalid for the current run state"),
    (ClaimConflictError, "claim_conflict", "execution claim conflicts with current state"),
    (ClaimExpiredError, "claim_expired", "execution claim has expired"),
    (StaleFenceError, "stale_fence", "execution fence is stale"),
    (CheckpointConflictError, "checkpoint_conflict", "checkpoint conflicts with current run state"),
    (DeliveryUnavailableError, "delivery_unavailable", "delivery backend is unavailable"),
    (SideEffectAmbiguousError, "side_effect_ambiguous", "side effect outcome is ambiguous"),
    (SideEffectInFlightError, "side_effect_in_flight", "side effect is still in flight"),
    (DistributedStoreClosedError, "distributed_store_closed", "distributed store is closed"),
    (
        DistributedBackendUnavailableError,
        "distributed_backend_unavailable",
        "distributed backend is unavailable",
    ),
)


@pytest.mark.parametrize(("error_type", "code", "message"), ERRORS)
def test_distributed_errors_have_stable_codes_and_safe_messages(
    error_type: type[Exception],
    code: str,
    message: str,
) -> None:
    error = error_type()

    assert error.code == code  # type: ignore[attr-defined]
    assert str(error) == message
    with pytest.raises(TypeError):
        error_type("postgres://user:password@host/db")
