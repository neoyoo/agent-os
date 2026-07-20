from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from agentos.artifacts import ArtifactRecord
from agentos.distributed.models import (
    ArtifactContent,
    RequestScope,
    RunReadModel,
)
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunStatus


NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)
ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"


def test_distributed_dtos_are_frozen_and_slotted() -> None:
    value = RunReadModel(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        status=RunStatus.RUNNING,
        wait_reason=None,
        aggregate_version=1,
        result=None,
    )

    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        value.run_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    [" tenant", "tenant ", "tenant id", "tenant\n", "x" * 256],
)
def test_request_scope_rejects_non_canonical_identifiers(value: str) -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        RequestScope(value, "user_1")


def test_run_read_model_carries_terminal_result_and_tenant_identity() -> None:
    result = AgentResult("quoted result")
    run = RunReadModel(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        status=RunStatus.COMPLETED,
        wait_reason=None,
        aggregate_version=4,
        result=result,
    )

    assert run.result is result
    with pytest.raises(ValueError, match="completed"):
        RunReadModel(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_2",
            status=RunStatus.COMPLETED,
            wait_reason=None,
            aggregate_version=1,
            result=None,
        )
    with pytest.raises(ValueError, match="non-completed"):
        RunReadModel(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_3",
            status=RunStatus.RUNNING,
            wait_reason=None,
            aggregate_version=1,
            result=result,
        )
    with pytest.raises(TypeError, match="wait_reason"):
        RunReadModel(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_4",
            status=RunStatus.WAITING,
            wait_reason="human_input",  # type: ignore[arg-type]
            aggregate_version=1,
            result=None,
        )
    with pytest.raises(TypeError, match="result"):
        RunReadModel(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_5",
            status=RunStatus.COMPLETED,
            wait_reason=None,
            aggregate_version=1,
            result=AgentResult(3),  # type: ignore[arg-type]
        )


def test_artifact_content_uses_canonical_metadata_and_immutable_bytes() -> None:
    record = ArtifactRecord(
        id=ARTIFACT_ID,
        session_id="session_1",
        filename="drawing.png",
        media_type="image/png",
        size_bytes=3,
        created_at=NOW,
    )

    content = ArtifactContent(record=record, data=b"abc")

    assert content.data == b"abc"
    with pytest.raises(TypeError, match="bytes"):
        ArtifactContent(record=record, data=bytearray(b"abc"))  # type: ignore[arg-type]
