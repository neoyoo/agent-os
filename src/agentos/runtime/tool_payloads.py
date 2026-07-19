from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace

from agentos.messages import StoredMessage, ToolCall
from agentos.providers import ProviderToolCall
from agentos.runtime.checkpoint import (
    CheckpointStoredMessage,
    CheckpointToolCall,
)
from agentos.runtime.errors import PayloadProtectorRequiredError
from agentos.runtime.execution import (
    PendingToolInvocation,
    RunExecutionCursor,
)
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    PayloadProtector,
    protect_payload,
    unprotect_payload,
)


@dataclass(slots=True)
class ToolPayloadRuntime:
    """管理 Tool arguments 的稳定 invocation 与受保护引用。"""

    protector: PayloadProtector | None
    context: PayloadProtectionContext
    _calls: dict[tuple[str, str], CheckpointToolCall] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @classmethod
    def for_session(cls, session_id: str) -> ToolPayloadRuntime:
        return cls(None, PayloadProtectionContext(None, session_id))

    def pending_cursor(
        self,
        *,
        run_id: str,
        turn_id: str,
        provider_call_index: int,
        assistant_message_id: str,
        calls: tuple[ProviderToolCall, ...],
    ) -> RunExecutionCursor:
        protector = self._require_protector()
        pending = []
        for tool_index, call in enumerate(calls):
            invocation_id = _invocation_id(
                self.context,
                run_id=run_id,
                turn_id=turn_id,
                provider_call_index=provider_call_index,
                tool_index=tool_index,
            )
            protection = self._execution_context(
                run_id=run_id,
                turn_id=turn_id,
                invocation_id=invocation_id,
                message_id=assistant_message_id,
                tool_call_id=call.id,
                tool_name=call.name,
            )
            key = (assistant_message_id, call.id)
            checkpoint_call = self._calls.get(key)
            if checkpoint_call is None:
                reference = protect_payload(
                    protector,
                    call.arguments,
                    context=protection,
                )
                checkpoint_call = CheckpointToolCall(
                    id=call.id,
                    name=call.name,
                    run_id=run_id,
                    turn_id=turn_id,
                    invocation_id=invocation_id,
                    invocation_ref=reference,
                )
                self._calls[key] = checkpoint_call
            else:
                self._validate_existing_call(
                    checkpoint_call,
                    message_id=assistant_message_id,
                    call=call,
                    run_id=run_id,
                    turn_id=turn_id,
                    invocation_id=invocation_id,
                )
                reference = checkpoint_call.invocation_ref
            pending.append(
                PendingToolInvocation(
                    invocation_id=invocation_id,
                    provider_tool_call_id=call.id,
                    tool_name=call.name,
                    invocation_ref=reference,
                ),
            )
        return RunExecutionCursor(
            turn_id=turn_id,
            stage="pending_tools",
            provider_call_index=provider_call_index,
            assistant_message_id=assistant_message_id,
            pending_tools=tuple(pending),
        )

    def checkpoint_messages(
        self,
        messages: list[StoredMessage],
        *,
        run_id: str | None,
        turn_id: str | None,
    ) -> tuple[CheckpointStoredMessage, ...]:
        result = []
        for message in messages:
            calls = tuple(
                self._checkpoint_call(message, call)
                for call in message.tool_calls
            )
            result.append(
                CheckpointStoredMessage(
                    id=message.id,
                    role=message.role,
                    content=message.content,
                    artifact_refs=message.artifact_refs,
                    tool_calls=calls,
                    tool_call_id=message.tool_call_id,
                ),
            )
        return tuple(result)

    def restore_messages(
        self,
        messages: tuple[CheckpointStoredMessage, ...],
    ) -> tuple[StoredMessage, ...]:
        restored = []
        for message in messages:
            calls = []
            for call in message.tool_calls:
                self._calls[(message.id, call.id)] = call
                payload = unprotect_payload(
                    self._require_protector(),
                    call.invocation_ref,
                    context=self._context_for(message.id, call),
                )
                calls.append(ToolCall(call.id, call.name, payload))
            restored.append(
                StoredMessage(
                    id=message.id,
                    role=message.role,  # type: ignore[arg-type]
                    content=message.content,
                    artifact_refs=message.artifact_refs,
                    tool_calls=tuple(calls),
                    tool_call_id=message.tool_call_id,
                ),
            )
        return tuple(restored)

    def restore_pending_calls(
        self,
        *,
        run_id: str,
        cursor: RunExecutionCursor,
    ) -> tuple[ProviderToolCall, ...]:
        if cursor.stage != "pending_tools" or cursor.assistant_message_id is None:
            return ()
        calls = []
        for pending in cursor.pending_tools:
            checkpoint_call = CheckpointToolCall(
                id=pending.provider_tool_call_id,
                name=pending.tool_name,
                run_id=run_id,
                turn_id=cursor.turn_id,
                invocation_id=pending.invocation_id,
                invocation_ref=pending.invocation_ref,
            )
            payload = unprotect_payload(
                self._require_protector(),
                pending.invocation_ref,
                context=self._context_for(
                    cursor.assistant_message_id,
                    checkpoint_call,
                ),
            )
            calls.append(
                ProviderToolCall(
                    pending.provider_tool_call_id,
                    pending.tool_name,
                    payload,
                ),
            )
        return tuple(calls)

    def current_run_id(self, cursor: RunExecutionCursor) -> str | None:
        if cursor.stage != "pending_tools" or cursor.assistant_message_id is None:
            return None
        first = self._calls.get(
            (cursor.assistant_message_id, cursor.pending_tools[0].provider_tool_call_id),
        ) if cursor.pending_tools else None
        return None if first is None else first.run_id

    def _checkpoint_call(
        self,
        message: StoredMessage,
        call: ToolCall,
    ) -> CheckpointToolCall:
        existing = self._calls.get((message.id, call.id))
        if existing is None:
            raise PayloadProtectorRequiredError(
                "tool payload was not protected at pending_tools",
            )
        self._validate_existing_call(
            existing,
            message_id=message.id,
            call=ProviderToolCall(call.id, call.name, call.arguments),
        )
        return existing

    def _validate_existing_call(
        self,
        existing: CheckpointToolCall,
        *,
        message_id: str,
        call: ProviderToolCall,
        run_id: str | None = None,
        turn_id: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        if (
            existing.id != call.id
            or existing.name != call.name
            or (run_id is not None and existing.run_id != run_id)
            or (turn_id is not None and existing.turn_id != turn_id)
            or (
                invocation_id is not None
                and existing.invocation_id != invocation_id
            )
        ):
            raise ValueError("protected tool invocation changed")
        restored = unprotect_payload(
            self._require_protector(),
            existing.invocation_ref,
            context=self._context_for(message_id, existing),
        )
        if restored != call.arguments:
            raise ValueError("protected tool invocation changed")

    def _context_for(
        self,
        message_id: str,
        call: CheckpointToolCall,
    ) -> PayloadProtectionContext:
        return self._execution_context(
            run_id=call.run_id,
            turn_id=call.turn_id,
            invocation_id=call.invocation_id,
            message_id=message_id,
            tool_call_id=call.id,
            tool_name=call.name,
        )

    def _execution_context(self, **values: str) -> PayloadProtectionContext:
        return replace(self.context, **values)

    def _require_protector(self) -> PayloadProtector:
        if self.protector is None:
            raise PayloadProtectorRequiredError(
                "persistent tool execution requires a payload protector",
            )
        return self.protector


def _invocation_id(
    context: PayloadProtectionContext,
    *,
    run_id: str,
    turn_id: str,
    provider_call_index: int,
    tool_index: int,
) -> str:
    identity = json.dumps(
        {
            "provider_call_index": provider_call_index,
            "run_id": run_id,
            "session_id": context.session_id,
            "tenant_id": context.tenant_id,
            "tool_index": tool_index,
            "turn_id": turn_id,
            "version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"invocation_{hashlib.sha256(identity).hexdigest()[:32]}"


__all__ = ["ToolPayloadRuntime"]
