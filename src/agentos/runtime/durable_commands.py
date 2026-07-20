from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.runtime.side_effect_resolution import (
    side_effect_resolution_from_payload,
    side_effect_resolution_to_payload,
)
from agentos.runtime.side_effect_types import SideEffectResolution


DurableRunCommandKind: TypeAlias = Literal[
    "resume",
    "wakeup",
    "retry",
    "hitl_answer",
    "resolve_side_effect",
    "cancel",
]
DurableContinuationKind: TypeAlias = Literal[
    "resume",
    "wakeup",
    "retry",
    "hitl_answer",
    "resolve_side_effect",
]

_COMMAND_KINDS = frozenset(
    {
        "resume",
        "wakeup",
        "retry",
        "hitl_answer",
        "resolve_side_effect",
        "cancel",
    },
)
_CONTINUATION_KINDS = frozenset(
    {"resume", "wakeup", "retry", "hitl_answer", "resolve_side_effect"},
)


@dataclass(frozen=True, slots=True, init=False)
class DurableRunCommand:
    """提交给 Durable Run 聚合根的持久命令。"""

    run_id: str
    command_id: str
    kind: DurableRunCommandKind
    payload: FrozenJsonObject

    def __init__(
        self,
        run_id: str,
        command_id: str,
        kind: DurableRunCommandKind,
        payload: dict[str, object] | FrozenJsonObject | SideEffectResolution | None = None,
    ) -> None:
        _require_identifier(run_id, "run_id")
        _require_identifier(command_id, "command_id")
        if kind not in _COMMAND_KINDS:
            raise ValueError("durable command kind is invalid")
        frozen = _normalize_payload(kind, payload)
        if kind == "hitl_answer" and not frozen:
            raise ValueError("hitl_answer payload must not be empty")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", frozen)


@dataclass(frozen=True, slots=True, init=False)
class AcceptedContinuationInput:
    """Store 已完成幂等校验的内部 continuation 输入。"""

    run_id: str
    command_id: str
    kind: DurableContinuationKind
    payload: FrozenJsonObject
    turn_id: str

    def __init__(
        self,
        run_id: str,
        command_id: str,
        kind: DurableContinuationKind,
        payload: dict[str, object] | FrozenJsonObject | None,
        turn_id: str,
    ) -> None:
        _require_identifier(run_id, "run_id")
        _require_identifier(command_id, "command_id")
        _require_identifier(turn_id, "turn_id")
        if kind not in _CONTINUATION_KINDS:
            raise ValueError("accepted continuation kind is invalid")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", _normalize_payload(kind, payload))
        object.__setattr__(self, "turn_id", turn_id)


@dataclass(frozen=True, slots=True)
class DurableCommandReceipt:
    """已经应用或去重的 Durable Command 回执。"""

    run_id: str
    command_id: str
    kind: DurableRunCommandKind
    aggregate_version: int
    duplicate: bool

    def __post_init__(self) -> None:
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.command_id, "command_id")
        if self.kind not in _COMMAND_KINDS:
            raise ValueError("durable command kind is invalid")
        _require_version(self.aggregate_version)
        if type(self.duplicate) is not bool:
            raise TypeError("duplicate must be bool")


def _require_identifier(value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_version(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("aggregate_version must be a non-negative integer")


def _normalize_payload(
    kind: str,
    payload: dict[str, object] | FrozenJsonObject | SideEffectResolution | None,
) -> FrozenJsonObject:
    if kind != "resolve_side_effect":
        if type(payload) is SideEffectResolution:
            raise TypeError("SideEffectResolution requires resolve_side_effect")
        return freeze_json_mapping({} if payload is None else payload)
    if type(payload) is SideEffectResolution:
        return side_effect_resolution_to_payload(payload)
    if payload is None:
        raise TypeError(
            "resolve_side_effect payload must be SideEffectResolution or canonical payload",
        )
    resolution = side_effect_resolution_from_payload(payload)
    return side_effect_resolution_to_payload(resolution)


__all__ = [
    "AcceptedContinuationInput",
    "DurableCommandReceipt",
    "DurableContinuationKind",
    "DurableRunCommand",
    "DurableRunCommandKind",
]
