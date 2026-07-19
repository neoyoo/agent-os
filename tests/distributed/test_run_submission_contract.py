from dataclasses import FrozenInstanceError

import pytest

from agentos.distributed.models import RunSubmission, RunSubmissionReceipt


def test_run_submission_is_immutable_and_preserves_artifact_order() -> None:
    submission = RunSubmission(
        session_id="session_1",
        submission_id="request_1",
        content="inspect",
        artifact_handles=("art_2", "art_1"),
    )

    assert submission.artifact_handles == ("art_2", "art_1")
    with pytest.raises(FrozenInstanceError):
        submission.content = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("field_name", ["session_id", "submission_id"])
def test_run_submission_requires_identifiers(field_name: str) -> None:
    values = {
        "session_id": "session_1",
        "submission_id": "request_1",
        "content": "inspect",
    }
    values[field_name] = " "

    with pytest.raises(ValueError, match=field_name):
        RunSubmission(**values)


def test_run_submission_rejects_non_string_content_and_handles() -> None:
    with pytest.raises(TypeError, match="content"):
        RunSubmission("session_1", "request_1", 3)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="artifact_handles"):
        RunSubmission(
            "session_1",
            "request_1",
            "inspect",
            artifact_handles=("art_1", 2),  # type: ignore[arg-type]
        )


def test_run_submission_receipt_validates_version_and_duplicate_flag() -> None:
    receipt = RunSubmissionReceipt(
        session_id="session_1",
        run_id="run_1",
        submission_id="request_1",
        aggregate_version=1,
        duplicate=False,
    )

    assert receipt.aggregate_version == 1
    with pytest.raises(ValueError, match="aggregate_version"):
        RunSubmissionReceipt("session_1", "run_1", "request_1", -1, False)
    with pytest.raises(TypeError, match="duplicate"):
        RunSubmissionReceipt(
            "session_1",
            "run_1",
            "request_1",
            1,
            duplicate=1,  # type: ignore[arg-type]
        )
