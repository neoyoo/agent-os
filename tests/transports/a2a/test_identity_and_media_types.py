from __future__ import annotations

from agentos.transports.a2a.identity import (
    a2a_artifact_upload_id,
    a2a_inline_push_identity,
    a2a_operation_identity,
    a2a_session_id,
)
from agentos.transports.a2a.protocol import (
    A2A_JSON_RPC_MEDIA_TYPE,
    A2A_PUSH_MEDIA_TYPE,
    A2A_SSE_MEDIA_TYPE,
)


def test_media_types_match_official_bindings() -> None:
    assert A2A_JSON_RPC_MEDIA_TYPE == "application/json"
    assert A2A_SSE_MEDIA_TYPE == "text/event-stream"
    assert A2A_PUSH_MEDIA_TYPE == "application/a2a+json"


def test_operation_identity_is_typed_deterministic_and_resource_scoped() -> None:
    first = a2a_operation_identity(
        method="CancelTask",
        request_id=7,
        task_id="run_1",
        config_id=None,
    )
    assert first == a2a_operation_identity(
        method="CancelTask",
        request_id=7,
        task_id="run_1",
        config_id=None,
    )
    assert first.startswith("a2a_op_")
    assert first != a2a_operation_identity(
        method="CancelTask",
        request_id="7",
        task_id="run_1",
        config_id=None,
    )
    assert first != a2a_operation_identity(
        method="CancelTask",
        request_id=7,
        task_id="run_2",
        config_id=None,
    )


def test_send_derived_identities_follow_the_frozen_hash_inputs() -> None:
    assert a2a_session_id(tenant_id="tenant_1", message_id="message_1") == (
        "a2a_123b11293751ba49d8bcd8c52491f2f3db896d21bc4590a4b3990c9044826596"
    )
    assert a2a_artifact_upload_id(message_id="message_1", part_index=2) == (
        "a2a_artifact_0cf318618efd1f8c0e142119cc284e202fc29608f020b1b413b2321a20474b0c"
    )
    assert a2a_inline_push_identity(message_id="message_1", task_id="run_1") == (
        "a2a_inline_push_6dc7887cc13b608a1c3a692a899b97b32a71d6f4921a3d60f0755c144016f25c"
    )
