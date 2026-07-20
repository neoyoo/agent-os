from dataclasses import FrozenInstanceError

import pytest

from agentos.distributed.models import (
    RequestScope,
    RunSubmission,
    RunSubmissionReceipt,
    canonical_submission_digest,
)


ARTIFACT_1 = "art_00000000-0000-4000-8000-000000000001"
ARTIFACT_2 = "art_00000000-0000-4000-8000-000000000002"


def test_run_submission_is_immutable_and_preserves_artifact_order() -> None:
    submission = RunSubmission(
        session_id="session_1",
        submission_id="request_1",
        content="inspect",
        artifact_handles=(ARTIFACT_2, ARTIFACT_1),
    )

    assert submission.artifact_handles == (ARTIFACT_2, ARTIFACT_1)
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
            artifact_handles=(ARTIFACT_1, 2),  # type: ignore[arg-type]
        )


def test_run_submission_requires_canonical_artifact_handles() -> None:
    with pytest.raises(ValueError, match="artifact"):
        RunSubmission(
            "session_1",
            "request_1",
            "inspect",
            artifact_handles=("art_1",),
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


def test_submission_digest_is_versioned_stable_and_excludes_principal() -> None:
    submission = RunSubmission(
        session_id="session_1",
        submission_id="request_1",
        content="inspect",
        artifact_handles=(ARTIFACT_2, ARTIFACT_1),
    )

    first = canonical_submission_digest(
        RequestScope("tenant_1", "user_1"),
        submission,
    )
    second = canonical_submission_digest(
        RequestScope("tenant_1", "service_account_1"),
        RunSubmission(
            session_id="session_1",
            submission_id="another_request_id",
            content="inspect",
            artifact_handles=(ARTIFACT_2, ARTIFACT_1),
        ),
    )

    assert first == "980dcda04922ad0b8166fd719f64943fe05a2045059c02e0e4bcc3b13a6bfeff"
    assert second == first


def test_submission_digest_binds_tenant_and_ordered_artifact_handles() -> None:
    submission = RunSubmission(
        "session_1",
        "request_1",
        "inspect",
        (ARTIFACT_2, ARTIFACT_1),
    )

    baseline = canonical_submission_digest(RequestScope("tenant_1", "user_1"), submission)
    other_tenant = canonical_submission_digest(RequestScope("tenant_2", "user_1"), submission)
    other_order = canonical_submission_digest(
        RequestScope("tenant_1", "user_1"),
        RunSubmission(
            "session_1",
            "request_1",
            "inspect",
            (ARTIFACT_1, ARTIFACT_2),
        ),
    )

    assert other_tenant != baseline
    assert other_order != baseline
