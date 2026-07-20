from __future__ import annotations

from typing import TypeAlias

from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.errors import RunProtocolError
from agentos.runtime.execution import (
    AcceptedStartInput,
    AcceptedTurnExecution,
    AcceptedTurnPreparation,
    ApplyAcceptedInput,
    RestoreAcceptedTurn,
)
from agentos.runtime.run import LocalContinuationInput, UserTurnInput
from agentos.runtime.side_effect_resume import SideEffectResume
from agentos.runtime.stream_events import TurnStreamEvent
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle


TurnExecutionInput: TypeAlias = (
    UserTurnInput
    | LocalContinuationInput
    | AcceptedStartInput
    | AcceptedContinuationInput
)


def turn_execution_input(
    input: UserTurnInput | LocalContinuationInput | AcceptedTurnExecution,
) -> TurnExecutionInput:
    """解包 accepted execution，保留 canonical turn input。"""

    if type(input) is AcceptedTurnExecution:
        return input.input
    return input


async def prepare_execution_turn(
    *,
    turns: TurnLifecycle,
    input: TurnExecutionInput,
    preparation: AcceptedTurnPreparation,
) -> tuple[TurnState | None, tuple[TurnStreamEvent, ...]]:
    """按唯一 preparation 创建或恢复内存 Turn。"""

    if type(preparation) is ApplyAcceptedInput:
        if type(input) is AcceptedStartInput:
            return await turns.prepare_user_turn(
                input.input,
                turn_id=input.turn_id,
                user_message_id=input.user_message_id,
            )
        if type(input) is UserTurnInput:
            return await turns.prepare_user_turn(input)
        return turns.prepare_continuation_turn(input)
    if type(preparation) is SideEffectResume:
        if type(input) is not AcceptedContinuationInput:
            raise RunProtocolError(
                "side effect resume requires accepted continuation input",
            )
        return turns.prepare_continuation_turn(input)
    if type(preparation) is not RestoreAcceptedTurn or not isinstance(
        input,
        (AcceptedStartInput, AcceptedContinuationInput),
    ):
        raise RunProtocolError("only accepted input can restore a prepared turn")
    return await turns.restore_turn(input), ()


__all__ = [
    "TurnExecutionInput",
    "prepare_execution_turn",
    "turn_execution_input",
]
