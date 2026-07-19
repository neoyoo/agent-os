from dataclasses import FrozenInstanceError

import pytest

from agentos.runtime.run_runtime import RunWriteGuard


def test_run_write_guard_is_immutable_and_requires_a_version() -> None:
    guard = RunWriteGuard(expected_version=3)

    assert guard.expected_version == 3
    assert guard.claim_id is None
    assert guard.fencing_token is None
    with pytest.raises(FrozenInstanceError):
        guard.expected_version = 4  # type: ignore[misc]


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_run_write_guard_rejects_invalid_versions(value: object) -> None:
    with pytest.raises(ValueError, match="expected_version"):
        RunWriteGuard(expected_version=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("claim_id", "fencing_token"),
    [("claim_1", None), (None, 1), (" ", 1), ("claim_1", 0)],
)
def test_run_write_guard_requires_a_complete_valid_fence(
    claim_id: str | None,
    fencing_token: int | None,
) -> None:
    with pytest.raises(ValueError, match="claim_id and fencing_token"):
        RunWriteGuard(
            expected_version=3,
            claim_id=claim_id,
            fencing_token=fencing_token,
        )


def test_run_write_guard_accepts_a_distributed_fence() -> None:
    guard = RunWriteGuard(
        expected_version=3,
        claim_id="claim_1",
        fencing_token=7,
    )

    assert guard.claim_id == "claim_1"
    assert guard.fencing_token == 7
