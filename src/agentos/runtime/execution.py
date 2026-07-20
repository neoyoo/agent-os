from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, Union

from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard

if TYPE_CHECKING:
    from agentos.runtime.run import UserTurnInput
    from agentos.runtime.side_effect_resume import SideEffectResume


ExecutionCheckpointStage: TypeAlias = Literal[
    "before_provider",
    "pending_tools",
    "after_tools",
]

_CHECKPOINT_STAGES = frozenset(
    {"before_provider", "pending_tools", "after_tools"},
)


@dataclass(frozen=True, slots=True)
class AcceptedStartInput:
    """Store 已持久接受且分配稳定消息标识的首次用户输入。"""

    run_id: str
    submission_id: str
    input: UserTurnInput
    turn_id: str
    user_message_id: str

    def __post_init__(self) -> None:
        from agentos.runtime.run import UserTurnInput

        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.submission_id, "submission_id")
        _require_identifier(self.turn_id, "turn_id")
        _require_identifier(self.user_message_id, "user_message_id")
        if type(self.input) is not UserTurnInput:
            raise TypeError("input must be UserTurnInput")


AcceptedTurnInput: TypeAlias = AcceptedStartInput | AcceptedContinuationInput


@dataclass(frozen=True, slots=True)
class ApplyAcceptedInput:
    """首次把已接受输入应用到当前 Turn。"""


@dataclass(frozen=True, slots=True)
class RestoreAcceptedTurn:
    """从已持久化 execution cursor 恢复当前 Turn。"""

    cursor: RunExecutionCursor

    def __post_init__(self) -> None:
        if type(self.cursor) is not RunExecutionCursor:
            raise TypeError("cursor must be RunExecutionCursor")


AcceptedTurnPreparation: TypeAlias = Union[
    ApplyAcceptedInput,
    RestoreAcceptedTurn,
    "SideEffectResume",
]


@dataclass(frozen=True, slots=True)
class AcceptedTurnExecution:
    """一次已领取输入、不可变写入权限及唯一恢复判别。"""

    input: AcceptedTurnInput
    guard: RunWriteGuard
    preparation: AcceptedTurnPreparation

    def __post_init__(self) -> None:
        if type(self.input) not in {
            AcceptedStartInput,
            AcceptedContinuationInput,
        }:
            raise TypeError("input must be an accepted turn input")
        if type(self.guard) is not RunWriteGuard:
            raise TypeError("guard must be RunWriteGuard")
        from agentos.runtime.side_effect_resume import SideEffectResume

        if type(self.preparation) not in {
            ApplyAcceptedInput,
            RestoreAcceptedTurn,
            SideEffectResume,
        }:
            raise TypeError("preparation must be an accepted turn preparation")
        self._validate_preparation()

    def _validate_preparation(self) -> None:
        from agentos.runtime.side_effect_resolution import (
            side_effect_resolution_from_payload,
        )
        from agentos.runtime.side_effect_resume import SideEffectResume

        preparation = self.preparation
        resolving = (
            type(self.input) is AcceptedContinuationInput
            and self.input.kind == "resolve_side_effect"
        )
        if not resolving:
            if type(preparation) is SideEffectResume:
                raise ValueError("typed side effect resume is not allowed")
            if (
                type(preparation) is RestoreAcceptedTurn
                and preparation.cursor.turn_id != self.input.turn_id
            ):
                raise ValueError("recovery cursor does not match prepared turn")
            return
        if type(preparation) is ApplyAcceptedInput:
            raise ValueError("typed side effect preparation is required")
        if type(preparation) is RestoreAcceptedTurn:
            if preparation.cursor.turn_id != self.input.turn_id:
                raise ValueError("recovery cursor does not match prepared turn")
            return
        resume = preparation
        assert type(resume) is SideEffectResume
        assert type(self.input) is AcceptedContinuationInput
        resolution = side_effect_resolution_from_payload(self.input.payload)
        if (
            resume.run_id != self.input.run_id
            or resume.continuation_turn_id != self.input.turn_id
            or resume.resolution != resolution
            or resume.record.claim_id != self.guard.claim_id
            or resume.record.fencing_token != self.guard.fencing_token
        ):
            raise ValueError("typed side effect resume does not match execution")


@dataclass(frozen=True, slots=True)
class PendingToolInvocation:
    """Provider tool call 与 SDK 稳定 invocation 的恢复映射。"""

    invocation_id: str
    provider_tool_call_id: str
    tool_name: str
    invocation_ref: ProtectedPayloadRef

    def __post_init__(self) -> None:
        _require_stable_id(self.invocation_id, "invocation", "invocation_id")
        _require_identifier(self.provider_tool_call_id, "provider_tool_call_id")
        _require_identifier(self.tool_name, "tool_name")
        if type(self.invocation_ref) is not ProtectedPayloadRef:
            raise TypeError("invocation_ref must be ProtectedPayloadRef")


@dataclass(frozen=True, slots=True)
class RunExecutionCursor:
    """一次 RUNNING execution 已越过的最后安全提交点。"""

    turn_id: str
    stage: ExecutionCheckpointStage
    provider_call_index: int
    assistant_message_id: str | None = None
    pending_tools: tuple[PendingToolInvocation, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.turn_id, "turn_id")
        if self.stage not in _CHECKPOINT_STAGES:
            raise ValueError("execution checkpoint stage is invalid")
        if (
            type(self.provider_call_index) is not int
            or self.provider_call_index < 0
        ):
            raise ValueError(
                "provider_call_index must be a non-negative integer",
            )
        pending = tuple(self.pending_tools)
        if any(type(item) is not PendingToolInvocation for item in pending):
            raise TypeError(
                "pending_tools must contain PendingToolInvocation values",
            )
        object.__setattr__(self, "pending_tools", pending)

        if self.stage == "before_provider":
            if self.assistant_message_id is not None or pending:
                raise ValueError(
                    "before_provider cursor cannot contain assistant tool state",
                )
            return
        _require_identifier(self.assistant_message_id, "assistant_message_id")
        if self.stage == "pending_tools" and not pending:
            raise ValueError("pending_tools cursor requires tool invocations")
        if self.stage == "after_tools" and pending:
            raise ValueError("after_tools cursor cannot contain pending invocations")
        invocation_ids = [item.invocation_id for item in pending]
        if len(set(invocation_ids)) != len(invocation_ids):
            raise ValueError("pending invocation ids must be unique")
        provider_call_ids = [item.provider_tool_call_id for item in pending]
        if len(set(provider_call_ids)) != len(provider_call_ids):
            raise ValueError("pending provider tool call ids must be unique")


def _require_identifier(value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_stable_id(value: object, prefix: str, field_name: str) -> None:
    expected_prefix = f"{prefix}_"
    if (
        type(value) is not str
        or len(value) != len(expected_prefix) + 32
        or not value.startswith(expected_prefix)
        or any(
            character not in "0123456789abcdef"
            for character in value[len(expected_prefix):]
        )
    ):
        raise ValueError(f"{field_name} must be a canonical {prefix} id")


__all__ = [
    "AcceptedStartInput",
    "AcceptedTurnExecution",
    "AcceptedTurnInput",
    "AcceptedTurnPreparation",
    "ApplyAcceptedInput",
    "ExecutionCheckpointStage",
    "PendingToolInvocation",
    "ProtectedPayloadRef",
    "RestoreAcceptedTurn",
    "RunExecutionCursor",
]
