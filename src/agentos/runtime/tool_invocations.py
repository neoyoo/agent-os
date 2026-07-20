from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentos.capabilities.invocation import ToolInvocation, ToolInvocationContext
from agentos.capabilities.tools import ToolExecutionContract
from agentos.providers import ProviderToolCall
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.tool_identity import invocation_id, operation_id


@dataclass(frozen=True, slots=True)
class ToolInvocationPlanEntry:
    """一个 Provider tool pair 对应的 canonical invocation。"""

    invocation: ToolInvocation
    invocation_ref: ProtectedPayloadRef | None = None

    def __post_init__(self) -> None:
        if type(self.invocation) is not ToolInvocation:
            raise TypeError("invocation must be ToolInvocation")
        if self.invocation_ref is not None and type(self.invocation_ref) is not ProtectedPayloadRef:
            raise TypeError("invocation_ref must be ProtectedPayloadRef or None")

    def provider_call(self) -> ProviderToolCall:
        context = self.invocation.context
        return ProviderToolCall(
            context.tool_call_id,
            self.invocation.tool_name,
            self.invocation.arguments,
        )


@dataclass(frozen=True, slots=True)
class ToolInvocationPlan:
    """同时驱动 pending checkpoint 与实际执行的唯一 Tool batch。"""

    provider_call_index: int
    assistant_message_id: str
    entries: tuple[ToolInvocationPlanEntry, ...]

    def __post_init__(self) -> None:
        if type(self.provider_call_index) is not int or self.provider_call_index < 0:
            raise ValueError("provider_call_index must be a non-negative integer")
        _require_identifier(self.assistant_message_id, "assistant_message_id")
        entries = tuple(self.entries)
        if not entries or any(type(entry) is not ToolInvocationPlanEntry for entry in entries):
            raise ValueError("entries must contain ToolInvocationPlanEntry values")
        object.__setattr__(self, "entries", entries)
        invocation_ids = [entry.invocation.context.invocation_id for entry in entries]
        if len(set(invocation_ids)) != len(invocation_ids):
            raise ValueError("tool invocation ids must be unique")
        call_ids = [entry.invocation.context.tool_call_id for entry in entries]
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("provider tool call ids must be unique")
        scopes = {
            (
                entry.invocation.context.tenant_id,
                entry.invocation.context.session_id,
                entry.invocation.context.run_id,
                entry.invocation.context.turn_id,
            )
            for entry in entries
        }
        if len(scopes) != 1:
            raise ValueError("tool invocation plan entries must share one scope")

    def provider_calls(self) -> tuple[ProviderToolCall, ...]:
        return tuple(entry.provider_call() for entry in self.entries)


@dataclass(frozen=True, slots=True)
class PreparedToolInvocationBatch:
    """已完成整批 metadata 与 wait-capable 预检的执行输入。"""

    plan: ToolInvocationPlan
    contracts: tuple[ToolExecutionContract, ...]

    def __post_init__(self) -> None:
        if type(self.plan) is not ToolInvocationPlan:
            raise TypeError("plan must be ToolInvocationPlan")
        contracts = tuple(self.contracts)
        if (
            len(contracts) != len(self.plan.entries)
            or any(type(item) is not ToolExecutionContract for item in contracts)
        ):
            raise ValueError("contracts must match tool invocation plan entries")
        object.__setattr__(self, "contracts", contracts)


def prepare_tool_invocation_batch(
    plan: ToolInvocationPlan,
    contract_for: Callable[[ToolInvocation], ToolExecutionContract],
) -> PreparedToolInvocationBatch:
    """在任何 hook、handler 或 Ledger 操作前完成整批预检。"""

    if type(plan) is not ToolInvocationPlan:
        raise TypeError("plan must be ToolInvocationPlan")
    contracts = tuple(
        contract_for(entry.invocation)
        for entry in plan.entries
    )
    if any(contract.wait_capable for contract in contracts) and len(contracts) != 1:
        raise ValueError("wait-capable tool batch must contain exactly one invocation")
    return PreparedToolInvocationBatch(plan, contracts)


def build_tool_invocation_plan(
    *,
    tenant_id: str | None,
    session_id: str,
    run_id: str,
    turn_id: str,
    provider_call_index: int,
    assistant_message_id: str,
    calls: tuple[ProviderToolCall, ...],
) -> ToolInvocationPlan:
    """把 Provider batch 一次性映射为稳定 ToolInvocationPlan。"""

    call_ids = [call.id for call in calls]
    if len(set(call_ids)) != len(call_ids):
        raise ValueError("provider tool call ids must be unique")
    entries = []
    for tool_index, call in enumerate(calls):
        stable_invocation_id = invocation_id(
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            provider_call_index=provider_call_index,
            tool_index=tool_index,
        )
        stable_operation_id = operation_id(
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            invocation_id=stable_invocation_id,
        )
        entries.append(
            ToolInvocationPlanEntry(
                ToolInvocation(
                    call.name,
                    call.arguments,
                    ToolInvocationContext(
                        invocation_id=stable_invocation_id,
                        operation_id=stable_operation_id,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        run_id=run_id,
                        turn_id=turn_id,
                        tool_call_id=call.id,
                        attempt=1,
                    ),
                ),
            ),
        )
    return ToolInvocationPlan(
        provider_call_index,
        assistant_message_id,
        tuple(entries),
    )


def _require_identifier(value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


__all__ = [
    "PreparedToolInvocationBatch",
    "ToolInvocationPlan",
    "ToolInvocationPlanEntry",
    "build_tool_invocation_plan",
    "prepare_tool_invocation_batch",
]
