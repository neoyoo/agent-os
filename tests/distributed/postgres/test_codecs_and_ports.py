from __future__ import annotations

import inspect
import json

import pytest

from agentos.artifacts import ArtifactRef
from agentos.capabilities.result_refs import ArtifactToolResultRef
from agentos._json_values import freeze_json_mapping
from agentos.capabilities.tools import SideEffectPolicy
from agentos.context import WorkingStateField
from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.postgres import (
    PostgresArtifactStore,
    PostgresClaimStore,
    PostgresOutboxStore,
    PostgresSideEffectStore,
    PostgresSideEffectResumeValidator,
    PostgresStateStore,
)
from agentos.distributed.postgres import claims as claims_module
from agentos.distributed.postgres._claim_records import accepted_input
from agentos.distributed.postgres._records import (
    session_checkpoint_from_json,
    session_checkpoint_to_json,
)
from agentos.distributed.postgres._side_effect_codec import (
    side_effect_record_from_json,
    side_effect_record_to_json,
)
from agentos.runtime.checkpoint import (
    CheckpointStoredMessage,
    ContextCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectStatus,
    SideEffectTransitionError,
)
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.tool_identity import canonical_digest
from agentos.runtime.tool_identity import compensation_operation_id


def test_postgres_side_effect_codec_preserves_tenant_and_fence() -> None:
    record = SideEffectRecord(
        attempt_id=SideEffectAttemptId(
            "tenant_1",
            "session_1",
            "operation_0123456789abcdef0123456789abcdef",
            1,
        ),
        run_id="run_1",
        turn_id="turn_1",
        invocation_id="invocation_0123456789abcdef0123456789abcdef",
        tool_name="lookup",
        policy=SideEffectPolicy.DEDUPLICATED,
        status=SideEffectStatus.RESERVED,
        invocation_digest="sha256:" + "a" * 64,
        invocation_ref=ProtectedPayloadRef("sealed", "payload-digest"),
        claim_id="claim_1",
        fencing_token=7,
    )

    encoded = side_effect_record_to_json(record)

    assert side_effect_record_from_json(encoded) == record
    assert "tenant_1" in encoded
    assert "claim_1" in encoded
    assert "arguments" not in encoded


def test_postgres_side_effect_codec_preserves_compensation_identity() -> None:
    operation_id = "operation_0123456789abcdef0123456789abcdef"
    record = SideEffectRecord(
        attempt_id=SideEffectAttemptId(
            "tenant_1",
            "session_1",
            operation_id,
            1,
        ),
        run_id="run_1",
        turn_id="turn_1",
        invocation_id="invocation_0123456789abcdef0123456789abcdef",
        tool_name="charge",
        policy=SideEffectPolicy.COMPENSATABLE,
        status=SideEffectStatus.COMPENSATING,
        invocation_digest="sha256:" + "a" * 64,
        invocation_ref=ProtectedPayloadRef("sealed", "payload-digest"),
        compensation_operation_id=compensation_operation_id(operation_id),
        compensation_attempt=2,
        claim_id="claim_1",
        fencing_token=7,
    )

    encoded = side_effect_record_to_json(record)
    decoded = side_effect_record_from_json(encoded)

    assert decoded == record
    assert decoded.compensation_attempt == 2


def test_postgres_side_effect_codec_rejects_oversized_artifact_preview() -> None:
    reference = ArtifactToolResultRef(
        ArtifactRef(
            "art_123e4567-e89b-42d3-a456-426614174000",
            "result.json",
            "application/json",
        ),
        "preview",
    )
    record = SideEffectRecord(
        attempt_id=SideEffectAttemptId(
            "tenant_1",
            "session_1",
            "operation_0123456789abcdef0123456789abcdef",
            1,
        ),
        run_id="run_1",
        turn_id="turn_1",
        invocation_id="invocation_0123456789abcdef0123456789abcdef",
        tool_name="lookup",
        policy=SideEffectPolicy.PURE,
        status=SideEffectStatus.COMPLETED,
        invocation_digest="sha256:" + "a" * 64,
        invocation_ref=ProtectedPayloadRef("sealed", "payload-digest"),
        result_ref=reference,
        result_digest=result_ref_digest(reference),
        outcome_kind=SideEffectOutcomeKind.PROVIDER_RESULT,
        claim_id="claim_1",
        fencing_token=7,
    )
    payload = json.loads(side_effect_record_to_json(record))
    oversized_preview = "x" * 4_097
    payload["result_ref"]["preview"] = oversized_preview
    payload["result_digest"] = canonical_digest(
        {
            "artifact": {
                "artifact_id": reference.artifact.artifact_id,
                "filename": reference.artifact.filename,
                "mime_type": reference.artifact.media_type,
            },
            "kind": "artifact",
            "preview": oversized_preview,
            "version": 1,
        },
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    with pytest.raises(SideEffectTransitionError):
        side_effect_record_from_json(encoded)


def test_session_checkpoint_codec_is_canonical_and_round_trips() -> None:
    checkpoint = SessionCheckpoint(
        session_id="session_1",
        session_status="running",
        next_turn_number=2,
        messages=(CheckpointStoredMessage("message_1", "assistant", "answer"),),
        active_refs=("message_1",),
        context=ContextCheckpoint(
            schema=(WorkingStateField("goal", "string", "task goal"),),
            working_state=freeze_json_mapping({}),
            compressed_history=(),
            inherited_state=("decision",),
        ),
    )

    encoded = session_checkpoint_to_json(checkpoint)

    assert session_checkpoint_from_json(encoded) == checkpoint
    assert session_checkpoint_to_json(session_checkpoint_from_json(encoded)) == encoded


def test_postgres_adapters_expose_only_async_io_methods() -> None:
    methods = {
        PostgresStateStore: (
            "submit",
            "submit_command",
            "get_run",
            "commit_running",
            "commit_waiting",
            "commit_terminal",
        ),
        PostgresClaimStore: (
            "resolve_delivery",
            "claim_pending_turn",
            "heartbeat",
            "release",
            "recover_expired",
        ),
        PostgresOutboxStore: ("claim_batch", "mark_published", "release_claim"),
        PostgresSideEffectStore: (
            "reserve",
            "get",
            "mark_started",
            "complete",
            "mark_ambiguous",
            "begin_compensation",
            "complete_compensation",
            "resolve",
        ),
        PostgresSideEffectResumeValidator: ("validate",),
        PostgresArtifactStore: ("upload", "list", "read", "delete"),
    }
    for adapter, names in methods.items():
        for name in names:
            assert inspect.iscoroutinefunction(getattr(adapter, name))

    assert not hasattr(PostgresSideEffectStore, "validate_resume")


def test_claim_authority_uses_database_time_only() -> None:
    source = inspect.getsource(claims_module)

    assert "clock_timestamp()" in source
    assert "datetime.now" not in source
    assert "time.monotonic" not in source


@pytest.mark.parametrize("source_kind", ("command", "team_message"))
def test_unsafe_payload_hydration_uses_distributed_error(source_kind: str) -> None:
    row = {
        "source_kind": source_kind,
        "payload_json": '{"value":"' + ("A" * 128) + '"}',
        "continuation_kind": "resume",
        "run_id": "run_1",
        "source_id": "command_1",
        "turn_id": "turn_1",
    }

    with pytest.raises(ClaimConflictError):
        accepted_input(row)
