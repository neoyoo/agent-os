from dataclasses import replace

import pytest

from agentos._json_values import freeze_json_mapping
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime, ToolCall
from agentos.providers import ProviderToolCall
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.errors import PayloadProtectorRequiredError
from agentos.runtime.execution import PendingToolInvocation
from agentos.runtime.payloads import PayloadProtectionContext
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.runtime.session import SessionState
from agentos.security import FernetPayloadProtector


def _payload_runtime() -> ToolPayloadRuntime:
    protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
    return ToolPayloadRuntime(
        protector=protector,
        context=PayloadProtectionContext("tenant_1", "session_1"),
    )


def test_pending_cursor_and_checkpoint_reuse_one_protected_reference() -> None:
    payloads = _payload_runtime()
    calls = (
        ProviderToolCall(
            "provider_call_1",
            "lookup",
            freeze_json_mapping({"api_key": "secret", "query": "drawing"}),
        ),
    )
    session = SessionState("session_1")
    session.new_turn("hello", turn_id="turn_1")
    messages = MessageRuntime()
    messages.append_user("hello", message_id="message_1")
    assistant = messages.append_assistant(
        "",
        [ToolCall(call.id, call.name, call.arguments) for call in calls],
    )
    cursor = payloads.pending_cursor(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id=assistant.id,
        calls=calls,
    )
    checkpoint = RuntimeCheckpointSource(
        session,
        messages,
        ContextRuntime(),
        payloads=payloads,
    ).capture(execution_cursor=cursor)

    checkpoint_call = checkpoint.messages[1].tool_calls[0]
    assert checkpoint_call.invocation_ref is cursor.pending_tools[0].invocation_ref
    assert checkpoint_call.invocation_id == cursor.pending_tools[0].invocation_id
    restored = payloads.restore_messages(checkpoint.messages)
    assert restored[1].tool_calls[0].arguments == calls[0].arguments


def test_persistent_tool_checkpoint_requires_payload_protector() -> None:
    payloads = ToolPayloadRuntime(
        protector=None,
        context=PayloadProtectionContext(None, "session_1"),
    )

    try:
        payloads.pending_cursor(
            run_id="run_1",
            turn_id="turn_1",
            provider_call_index=0,
            assistant_message_id="message_1",
            calls=(ProviderToolCall("call_1", "lookup", {}),),
        )
    except PayloadProtectorRequiredError as error:
        assert str(error) == "persistent tool execution requires a payload protector"
    else:
        raise AssertionError("missing PayloadProtector must fail closed")


def test_pending_cursor_reentry_reuses_the_same_protected_reference() -> None:
    payloads = _payload_runtime()
    calls = (
        ProviderToolCall(
            "call_1",
            "lookup",
            freeze_json_mapping({"secret": "value"}),
        ),
    )
    kwargs = {
        "run_id": "run_1",
        "turn_id": "turn_1",
        "provider_call_index": 2,
        "assistant_message_id": "message_1",
        "calls": calls,
    }

    first = payloads.pending_cursor(**kwargs)  # type: ignore[arg-type]
    second = payloads.pending_cursor(**kwargs)  # type: ignore[arg-type]

    assert second.pending_tools[0].invocation_ref is (
        first.pending_tools[0].invocation_ref
    )


def test_pending_cursor_reentry_rejects_changed_arguments() -> None:
    payloads = _payload_runtime()
    kwargs = {
        "run_id": "run_1",
        "turn_id": "turn_1",
        "provider_call_index": 2,
        "assistant_message_id": "message_1",
    }
    payloads.pending_cursor(
        **kwargs,  # type: ignore[arg-type]
        calls=(ProviderToolCall("call_1", "lookup", {"value": 1}),),
    )

    with pytest.raises(ValueError, match="protected tool invocation changed"):
        payloads.pending_cursor(
            **kwargs,  # type: ignore[arg-type]
            calls=(ProviderToolCall("call_1", "lookup", {"value": 2}),),
        )


def test_invocation_identity_distinguishes_absent_and_literal_local_tenant() -> None:
    protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
    values = []
    for tenant_id in (None, "local"):
        runtime = ToolPayloadRuntime(
            protector,
            PayloadProtectionContext(tenant_id, "session_1"),
        )
        cursor = runtime.pending_cursor(
            run_id="run_1",
            turn_id="turn_1",
            provider_call_index=0,
            assistant_message_id="message_1",
            calls=(ProviderToolCall("call_1", "lookup", {}),),
        )
        values.append(cursor.pending_tools[0].invocation_id)

    assert values[0] != values[1]


def test_invocation_identity_does_not_depend_on_assistant_message_id() -> None:
    payloads = _payload_runtime()
    call = ProviderToolCall("call_1", "lookup", {"query": "drawing"})

    first = payloads.pending_cursor(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=2,
        assistant_message_id="message_before_crash",
        calls=(call,),
    )
    recovered = payloads.pending_cursor(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=2,
        assistant_message_id="message_after_crash",
        calls=(call,),
    )

    assert recovered.pending_tools[0].invocation_id == (
        first.pending_tools[0].invocation_id
    )


def test_pending_cursor_must_match_checkpoint_assistant_tool_calls() -> None:
    payloads = _payload_runtime()
    calls = (ProviderToolCall("call_1", "lookup", {"value": 1}),)
    session = SessionState("session_1")
    session.new_turn("hello", turn_id="turn_1")
    messages = MessageRuntime()
    messages.append_user("hello", message_id="message_1")
    assistant = messages.append_assistant(
        "",
        [ToolCall(call.id, call.name, call.arguments) for call in calls],
    )
    cursor = payloads.pending_cursor(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id=assistant.id,
        calls=calls,
    )
    checkpoint = RuntimeCheckpointSource(
        session,
        messages,
        ContextRuntime(),
        payloads,
    ).capture(execution_cursor=cursor)
    pending = cursor.pending_tools[0]
    forged = replace(
        cursor,
        pending_tools=(
            PendingToolInvocation(
                pending.invocation_id,
                pending.provider_tool_call_id,
                "another_tool",
                pending.invocation_ref,
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="pending tool cursor does not match assistant tool calls",
    ):
        replace(checkpoint, execution_cursor=forged)
