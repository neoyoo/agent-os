from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from agentos.capabilities.invocation import ToolInvocation, ToolInvocationContext
from agentos.capabilities.tools import ToolExecutionContract
from agentos.messages import MessageRuntime, StoredMessage, ToolCall
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
from agentos.runtime.tool_identity import invocation_id, operation_id
from agentos.runtime.tool_invocations import (
    PreparedToolInvocationBatch,
    ToolInvocationPlan,
    ToolInvocationPlanEntry,
    build_tool_invocation_plan,
    prepare_tool_invocation_batch,
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

    def build_plan(
        self,
        *,
        run_id: str,
        turn_id: str,
        provider_call_index: int,
        assistant_message_id: str,
        calls: tuple[ProviderToolCall, ...],
    ) -> ToolInvocationPlan:
        plan = build_tool_invocation_plan(
            tenant_id=self.context.tenant_id,
            session_id=self.context.session_id,
            run_id=run_id,
            turn_id=turn_id,
            provider_call_index=provider_call_index,
            assistant_message_id=assistant_message_id,
            calls=calls,
        )
        return self.protect_plan(plan)

    def prepare_batch(
        self,
        *,
        run_id: str,
        turn_id: str,
        provider_call_index: int,
        assistant_message_id: str,
        calls: tuple[ProviderToolCall, ...],
        contract_for: Callable[[ToolInvocation], ToolExecutionContract],
    ) -> PreparedToolInvocationBatch:
        plan = build_tool_invocation_plan(
            tenant_id=self.context.tenant_id,
            session_id=self.context.session_id,
            run_id=run_id,
            turn_id=turn_id,
            provider_call_index=provider_call_index,
            assistant_message_id=assistant_message_id,
            calls=calls,
        )
        batch = prepare_tool_invocation_batch(plan, contract_for)
        protected = self.protect_plan(plan)
        return PreparedToolInvocationBatch(protected, batch.contracts)

    def protect_plan(self, plan: ToolInvocationPlan) -> ToolInvocationPlan:
        if type(plan) is not ToolInvocationPlan:
            raise TypeError("plan must be ToolInvocationPlan")
        protector = self.protector
        if protector is None:
            return plan
        entries = []
        protected_calls: dict[tuple[str, str], CheckpointToolCall] = {}
        for entry in plan.entries:
            invocation = entry.invocation
            context = invocation.context
            call = entry.provider_call()
            protection = self._execution_context(
                run_id=context.run_id,
                turn_id=context.turn_id,
                invocation_id=context.invocation_id,
                message_id=plan.assistant_message_id,
                tool_call_id=context.tool_call_id,
                tool_name=invocation.tool_name,
            )
            key = (plan.assistant_message_id, call.id)
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
                    run_id=context.run_id,
                    turn_id=context.turn_id,
                    invocation_id=context.invocation_id,
                    invocation_ref=reference,
                )
                protected_calls[key] = checkpoint_call
            else:
                self._validate_existing_call(
                    checkpoint_call,
                    message_id=plan.assistant_message_id,
                    call=call,
                    run_id=context.run_id,
                    turn_id=context.turn_id,
                    invocation_id=context.invocation_id,
                )
                reference = checkpoint_call.invocation_ref
            entries.append(replace(entry, invocation_ref=reference))
        self._calls.update(protected_calls)
        return replace(plan, entries=tuple(entries))

    def pending_cursor(self, plan: ToolInvocationPlan) -> RunExecutionCursor:
        self._require_protector()
        pending = []
        for entry in plan.entries:
            context = entry.invocation.context
            reference = entry.invocation_ref
            if reference is None:
                raise PayloadProtectorRequiredError(
                    "persistent tool execution requires a payload protector",
                )
            pending.append(
                PendingToolInvocation(
                    invocation_id=context.invocation_id,
                    provider_tool_call_id=context.tool_call_id,
                    tool_name=entry.invocation.tool_name,
                    invocation_ref=reference,
                )
            )
        return RunExecutionCursor(
            turn_id=plan.entries[0].invocation.context.turn_id,
            stage="pending_tools",
            provider_call_index=plan.provider_call_index,
            assistant_message_id=plan.assistant_message_id,
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

    def restore_pending_plan(
        self,
        *,
        run_id: str,
        cursor: RunExecutionCursor,
    ) -> ToolInvocationPlan:
        if cursor.stage != "pending_tools" or cursor.assistant_message_id is None:
            raise ValueError("pending tool recovery requires a pending_tools cursor")
        entries = []
        for tool_index, pending in enumerate(cursor.pending_tools):
            expected_invocation_id = invocation_id(
                tenant_id=self.context.tenant_id,
                session_id=self.context.session_id,
                run_id=run_id,
                turn_id=cursor.turn_id,
                provider_call_index=cursor.provider_call_index,
                tool_index=tool_index,
            )
            if pending.invocation_id != expected_invocation_id:
                raise ValueError("pending tool invocation identity is invalid")
            checkpoint_call = CheckpointToolCall(
                id=pending.provider_tool_call_id,
                name=pending.tool_name,
                run_id=run_id,
                turn_id=cursor.turn_id,
                invocation_id=pending.invocation_id,
                invocation_ref=pending.invocation_ref,
            )
            self._calls[(cursor.assistant_message_id, pending.provider_tool_call_id)] = (
                checkpoint_call
            )
            payload = unprotect_payload(
                self._require_protector(),
                pending.invocation_ref,
                context=self._context_for(
                    cursor.assistant_message_id,
                    checkpoint_call,
                ),
            )
            stable_operation_id = operation_id(
                tenant_id=self.context.tenant_id,
                session_id=self.context.session_id,
                run_id=run_id,
                turn_id=cursor.turn_id,
                invocation_id=pending.invocation_id,
            )
            entries.append(
                ToolInvocationPlanEntry(
                    ToolInvocation(
                        pending.tool_name,
                        payload,
                        ToolInvocationContext(
                            invocation_id=pending.invocation_id,
                            operation_id=stable_operation_id,
                            tenant_id=self.context.tenant_id,
                            session_id=self.context.session_id,
                            run_id=run_id,
                            turn_id=cursor.turn_id,
                            tool_call_id=pending.provider_tool_call_id,
                            attempt=1,
                        ),
                    ),
                    pending.invocation_ref,
                ),
            )
        return ToolInvocationPlan(
            cursor.provider_call_index,
            cursor.assistant_message_id,
            tuple(entries),
        )

    def restore_completed_plan(
        self,
        *,
        run_id: str,
        cursor: RunExecutionCursor,
        messages: MessageRuntime,
    ) -> ToolInvocationPlan | None:
        """Rebuild an after_tools batch for ephemeral result projection only."""

        if cursor.stage != "after_tools" or cursor.assistant_message_id is None:
            raise ValueError("completed tool recovery requires an after_tools cursor")
        try:
            assistant = messages.store.get(cursor.assistant_message_id)
        except KeyError:
            raise ValueError("completed tool assistant message is missing") from None
        if assistant.role != "assistant" or not assistant.tool_calls:
            raise ValueError("completed tool assistant message is invalid")
        if all(call.name != "load_attachment" for call in assistant.tool_calls):
            return None
        return self.build_plan(
            run_id=run_id,
            turn_id=cursor.turn_id,
            provider_call_index=cursor.provider_call_index,
            assistant_message_id=assistant.id,
            calls=tuple(
                ProviderToolCall(call.id, call.name, call.arguments)
                for call in assistant.tool_calls
            ),
        )

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


__all__ = ["ToolPayloadRuntime"]
