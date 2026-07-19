import pytest

from agentos.runtime.execution import (
    PendingToolInvocation,
    ProtectedPayloadRef,
    RunExecutionCursor,
)


def test_protected_payload_reference_redacts_its_token() -> None:
    reference = ProtectedPayloadRef(
        token="encrypted-envelope",
        digest="sha256:digest",
    )

    assert reference.token == "encrypted-envelope"
    assert "encrypted-envelope" not in repr(reference)


def test_pending_tools_cursor_preserves_provider_pairing_and_sdk_identity() -> None:
    pending = PendingToolInvocation(
        invocation_id="invocation_1",
        provider_tool_call_id="provider_call_9",
        tool_name="lookup",
        invocation_ref=ProtectedPayloadRef(
            token="encrypted-envelope",
            digest="sha256:digest",
        ),
    )

    cursor = RunExecutionCursor(
        turn_id="turn_1",
        stage="pending_tools",
        provider_call_index=2,
        assistant_message_id="message_2",
        pending_tools=(pending,),
    )

    assert cursor.pending_tools[0].invocation_id == "invocation_1"
    assert cursor.pending_tools[0].provider_tool_call_id == "provider_call_9"


@pytest.mark.parametrize(
    "cursor",
    [
        RunExecutionCursor(
            turn_id="turn_1",
            stage="before_provider",
            provider_call_index=0,
        ),
        RunExecutionCursor(
            turn_id="turn_1",
            stage="after_tools",
            provider_call_index=1,
            assistant_message_id="message_1",
        ),
    ],
)
def test_non_pending_cursor_stages_have_no_pending_invocations(
    cursor: RunExecutionCursor,
) -> None:
    assert cursor.pending_tools == ()


def test_cursor_rejects_duplicate_invocation_identity() -> None:
    reference = ProtectedPayloadRef(token="encrypted", digest="sha256:digest")
    pending = PendingToolInvocation("inv_1", "call_1", "lookup", reference)

    with pytest.raises(ValueError, match="unique"):
        RunExecutionCursor(
            turn_id="turn_1",
            stage="pending_tools",
            provider_call_index=1,
            assistant_message_id="message_1",
            pending_tools=(pending, pending),
        )
