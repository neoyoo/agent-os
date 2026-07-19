from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentos.distributed.models import ExecutionClaim


def test_execution_claim_normalizes_expiry_to_utc() -> None:
    claim = ExecutionClaim(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        owner_id="worker_1",
        claim_id="claim_1",
        fencing_token=3,
        expires_at=datetime(
            2026,
            7,
            19,
            20,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    assert claim.expires_at == datetime(2026, 7, 19, 12, tzinfo=UTC)


def test_execution_claim_requires_positive_fence_and_aware_expiry() -> None:
    values = {
        "tenant_id": "tenant_1",
        "session_id": "session_1",
        "run_id": "run_1",
        "owner_id": "worker_1",
        "claim_id": "claim_1",
        "fencing_token": 1,
        "expires_at": datetime(2026, 7, 19, 12, tzinfo=UTC),
    }

    with pytest.raises(ValueError, match="fencing_token"):
        ExecutionClaim(**{**values, "fencing_token": 0})
    with pytest.raises(ValueError, match="timezone-aware"):
        ExecutionClaim(
            **{**values, "expires_at": datetime(2026, 7, 19, 12)},
        )
