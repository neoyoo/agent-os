from dataclasses import replace

import pytest

from agentos._json_values import FrozenJsonObject
from agentos.providers import ProviderToolCall
from agentos.runtime.errors import PayloadProtectionError
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    ProtectedPayloadRef,
)
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.security import FernetPayloadProtector


def _payloads() -> ToolPayloadRuntime:
    return ToolPayloadRuntime(
        protector=FernetPayloadProtector(FernetPayloadProtector.generate_key()),
        context=PayloadProtectionContext("tenant_1", "session_1"),
    )


class FailSecondPayloadProtector:
    def __init__(self) -> None:
        self._delegate = FernetPayloadProtector(
            FernetPayloadProtector.generate_key(),
        )
        self.protection_calls = 0

    def protect(
        self,
        payload: FrozenJsonObject,
        *,
        context: PayloadProtectionContext,
    ) -> ProtectedPayloadRef:
        self.protection_calls += 1
        if self.protection_calls == 2:
            raise RuntimeError("injected second protection failure")
        return self._delegate.protect(payload, context=context)

    def unprotect(
        self,
        reference: ProtectedPayloadRef,
        *,
        context: PayloadProtectionContext,
    ) -> FrozenJsonObject:
        return self._delegate.unprotect(reference, context=context)


def test_one_plan_drives_checkpoint_and_recovered_execution_identity() -> None:
    payloads = _payloads()
    plan = payloads.build_plan(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(ProviderToolCall("call_1", "lookup", {"query": "drawing"}),),
    )

    cursor = payloads.pending_cursor(plan)
    recovered = payloads.restore_pending_plan(run_id="run_1", cursor=cursor)

    planned = plan.entries[0]
    restored = recovered.entries[0]
    assert cursor.pending_tools[0].invocation_id == (
        planned.invocation.context.invocation_id
    )
    assert restored.invocation == planned.invocation
    assert restored.invocation_ref == planned.invocation_ref
    assert restored.invocation.context.operation_id == (
        "operation_61caedfa427335744c5dd0b2db07dc80"
    )


def test_build_plan_does_not_cache_partially_protected_batch() -> None:
    protector = FailSecondPayloadProtector()
    payloads = ToolPayloadRuntime(
        protector,
        PayloadProtectionContext("tenant_1", "session_1"),
    )
    initial_calls = (
        ProviderToolCall("call_1", "lookup", {"query": "initial"}),
        ProviderToolCall("call_2", "lookup", {"query": "second"}),
    )

    with pytest.raises(PayloadProtectionError):
        payloads.build_plan(
            run_id="run_1",
            turn_id="turn_1",
            provider_call_index=0,
            assistant_message_id="message_1",
            calls=initial_calls,
        )

    recovered = payloads.build_plan(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(
            ProviderToolCall("call_1", "lookup", {"query": "replacement"}),
            initial_calls[1],
        ),
    )
    assert recovered.entries[0].invocation.arguments["query"] == "replacement"


def test_plan_validates_invocation_and_provider_call_ids_independently() -> None:
    payloads = _payloads()

    try:
        payloads.build_plan(
            run_id="run_1",
            turn_id="turn_1",
            provider_call_index=0,
            assistant_message_id="message_1",
            calls=(
                ProviderToolCall("call_1", "lookup", {}),
                ProviderToolCall("call_1", "other", {}),
            ),
        )
    except ValueError as error:
        assert str(error) == "provider tool call ids must be unique"
    else:
        raise AssertionError("duplicate provider call ids must fail closed")


def test_restore_rejects_invocation_id_not_matching_cursor_position() -> None:
    payloads = _payloads()
    plan = payloads.build_plan(
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(ProviderToolCall("call_1", "lookup", {}),),
    )
    cursor = payloads.pending_cursor(plan)
    forged = replace(
        cursor,
        pending_tools=(
            replace(
                cursor.pending_tools[0],
                invocation_id="invocation_00000000000000000000000000000000",
            ),
        ),
    )

    with pytest.raises(ValueError, match="invocation identity"):
        payloads.restore_pending_plan(run_id="run_1", cursor=forged)
