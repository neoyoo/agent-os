import json

import pytest

from agentos.multi import AgentEnvelope, TaskRecord, TaskRequest, TaskResult
from agentos.multi.serializers import (
    envelope_from_dict,
    envelope_to_dict,
    task_record_from_dict,
    task_record_to_dict,
    task_request_from_dict,
    task_request_to_dict,
    task_result_from_dict,
    task_result_to_dict,
)


def test_task_request_round_trips_json_safe_fields() -> None:
    request = TaskRequest(
        task_id="task_1",
        instruction="Do work",
        allowed_tool_names=("read", "write"),
        timeout_seconds=30.0,
        trace_context={"trace_id": "trace_1"},
    )

    data = task_request_to_dict(request)

    assert data["allowed_tool_names"] == ["read", "write"]
    assert task_request_from_dict(data) == request


def test_task_result_round_trips_json_safe_fields() -> None:
    result = TaskResult(
        task_id="task_1",
        status="completed",
        summary="done",
        artifacts={"path": "out.txt"},
        elapsed_seconds=2.5,
    )

    assert task_result_from_dict(task_result_to_dict(result)) == result


def test_task_result_rejects_non_json_artifacts() -> None:
    result = TaskResult(
        task_id="task_1",
        status="completed",
        summary="done",
        artifacts={"bad": object()},
    )

    with pytest.raises(TypeError, match="artifacts must be JSON serializable"):
        task_result_to_dict(result)


def test_task_result_serialized_form_can_be_encoded_as_json() -> None:
    result = TaskResult(
        task_id="task_1",
        status="completed",
        summary="done",
        artifacts={"nested": {"count": 1, "ok": True}},
    )

    json.dumps(task_result_to_dict(result))


def test_task_result_rejects_non_standard_json_numbers() -> None:
    result = TaskResult(
        task_id="task_1",
        status="completed",
        summary="done",
        artifacts={"bad": float("nan")},
    )

    with pytest.raises(TypeError, match="artifacts must be JSON serializable"):
        task_result_to_dict(result)


def test_task_result_from_dict_rejects_non_json_artifacts() -> None:
    data = task_result_to_dict(
        TaskResult(task_id="task_1", status="completed", summary="done"),
    )
    data["artifacts"] = {"bad": float("inf")}

    with pytest.raises(TypeError, match="artifacts must be JSON serializable"):
        task_result_from_dict(data)


def test_task_record_round_trips_distributed_metadata() -> None:
    record = TaskRecord(
        task_id="task_1",
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="worker-capability",
        request=TaskRequest(task_id="task_1", instruction="Do work"),
        status="running",
        created_at=1.0,
        deadline_at=30.0,
        worker_id="worker-instance-1",
        lease_expires_at=20.0,
        attempt=2,
        updated_at=3.0,
        version=4,
        cancel_requested_at=5.0,
        result_notified_at=6.0,
    )

    assert task_record_from_dict(task_record_to_dict(record)) == record


def test_task_record_from_dict_uses_backwards_compatible_metadata_defaults() -> None:
    record = TaskRecord(
        task_id="task_1",
        mode="spawn",
        parent_agent_id="parent",
        target_agent_id="child",
        request=TaskRequest(task_id="task_1", instruction="Do work"),
        status="queued",
        created_at=1.0,
        deadline_at=30.0,
    )
    data = task_record_to_dict(record)
    for key in (
        "worker_id",
        "lease_expires_at",
        "attempt",
        "updated_at",
        "version",
        "cancel_requested_at",
        "result_notified_at",
    ):
        del data[key]

    assert task_record_from_dict(data) == record


def test_envelope_round_trips_task_request_and_result_payloads() -> None:
    request_envelope = AgentEnvelope(
        envelope_id="env_req",
        from_agent_id="parent",
        to_agent_id="worker",
        type="task_request",
        payload=TaskRequest(task_id="task_1", instruction="Do work"),
        created_at=1.0,
        correlation_id="task_1",
    )
    result_envelope = AgentEnvelope(
        envelope_id="env_res",
        from_agent_id="worker",
        to_agent_id="parent",
        type="task_result",
        payload=TaskResult(task_id="task_1", status="completed", summary="done"),
        created_at=2.0,
        correlation_id="task_1",
    )

    assert envelope_from_dict(envelope_to_dict(request_envelope)) == request_envelope
    assert envelope_from_dict(envelope_to_dict(result_envelope)) == result_envelope


def test_deserializers_reject_unknown_literal_values() -> None:
    record = task_record_to_dict(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_1", instruction="Do work"),
            status="queued",
            created_at=1.0,
            deadline_at=30.0,
        ),
    )
    record["mode"] = "bogus"
    with pytest.raises(ValueError, match="invalid coordination mode"):
        task_record_from_dict(record)

    result = task_result_to_dict(
        TaskResult(task_id="task_1", status="completed", summary="done"),
    )
    result["status"] = "bogus"
    with pytest.raises(ValueError, match="invalid task status"):
        task_result_from_dict(result)

    envelope = envelope_to_dict(
        AgentEnvelope(
            envelope_id="env_1",
            from_agent_id="parent",
            to_agent_id="worker",
            type="task_request",
            payload=TaskRequest(task_id="task_1", instruction="Do work"),
            created_at=1.0,
        ),
    )
    envelope["type"] = "bogus"
    with pytest.raises(ValueError, match="invalid envelope type"):
        envelope_from_dict(envelope)


def test_serializers_reject_unknown_literal_values() -> None:
    result = TaskResult(
        task_id="task_1",
        status="bogus",  # type: ignore[arg-type]
        summary="done",
    )
    with pytest.raises(ValueError, match="invalid task status"):
        task_result_to_dict(result)

    record = TaskRecord(
        task_id="task_1",
        mode="bogus",  # type: ignore[arg-type]
        parent_agent_id="parent",
        target_agent_id="worker",
        request=TaskRequest(task_id="task_1", instruction="Do work"),
        status="queued",
        created_at=1.0,
        deadline_at=30.0,
    )
    with pytest.raises(ValueError, match="invalid coordination mode"):
        task_record_to_dict(record)


def test_envelope_to_dict_rejects_type_payload_mismatch() -> None:
    envelope = AgentEnvelope(
        envelope_id="env_1",
        from_agent_id="parent",
        to_agent_id="worker",
        type="task_request",
        payload=TaskResult(task_id="task_1", status="completed", summary="done"),
        created_at=1.0,
    )

    with pytest.raises(TypeError, match="task_request envelope payload"):
        envelope_to_dict(envelope)


def test_envelope_from_dict_rejects_type_payload_mismatch() -> None:
    envelope = envelope_to_dict(
        AgentEnvelope(
            envelope_id="env_1",
            from_agent_id="worker",
            to_agent_id="parent",
            type="task_result",
            payload=TaskResult(task_id="task_1", status="completed", summary="done"),
            created_at=1.0,
        ),
    )
    envelope["type"] = "task_request"

    with pytest.raises(TypeError, match="task_request envelope payload"):
        envelope_from_dict(envelope)


def test_envelope_from_dict_rejects_ambiguous_payload_shape() -> None:
    envelope = envelope_to_dict(
        AgentEnvelope(
            envelope_id="env_1",
            from_agent_id="parent",
            to_agent_id="worker",
            type="task_request",
            payload=TaskRequest(task_id="task_1", instruction="Do work"),
            created_at=1.0,
        ),
    )
    envelope["payload"]["status"] = "completed"
    envelope["payload"]["summary"] = "done"

    with pytest.raises(TypeError, match="task_request envelope payload"):
        envelope_from_dict(envelope)
