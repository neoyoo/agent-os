from __future__ import annotations

from dataclasses import dataclass, replace
import unicodedata

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)


@dataclass(frozen=True, slots=True)
class ToolInvocationContext:
    """一次 Tool operation 传给业务 handler 的稳定执行身份。"""

    invocation_id: str
    operation_id: str
    tenant_id: str | None
    session_id: str
    run_id: str
    turn_id: str
    tool_call_id: str
    attempt: int

    def __post_init__(self) -> None:
        _require_stable_id(self.invocation_id, "invocation", "invocation_id")
        _require_stable_id(self.operation_id, "operation", "operation_id")
        if self.tenant_id is not None:
            _require_identifier(self.tenant_id, "tenant_id")
        _require_identifier(self.session_id, "session_id")
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.turn_id, "turn_id")
        _require_identifier(self.tool_call_id, "tool_call_id")
        _require_attempt(self.attempt)


@dataclass(frozen=True, slots=True, init=False)
class ToolInvocation:
    """Tool handler 的唯一不可变输入。"""

    tool_name: str
    arguments: FrozenJsonObject
    context: ToolInvocationContext

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, object] | FrozenJsonObject,
        context: ToolInvocationContext,
    ) -> None:
        _require_identifier(tool_name, "tool_name")
        if type(context) is not ToolInvocationContext:
            raise TypeError("context must be ToolInvocationContext")
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "arguments", freeze_json_mapping(arguments))
        object.__setattr__(self, "context", context)

    def with_attempt(self, attempt: int) -> ToolInvocation:
        """保留 operation identity，只更新 handler attempt。"""

        return ToolInvocation(
            self.tool_name,
            self.arguments,
            replace(self.context, attempt=attempt),
        )


@dataclass(frozen=True, slots=True)
class ToolCompensationContext:
    """补偿 handler 使用的稳定 operation 身份。"""

    original_operation_id: str
    compensation_operation_id: str
    tenant_id: str | None
    session_id: str
    run_id: str
    turn_id: str
    invocation_id: str
    attempt: int

    def __post_init__(self) -> None:
        _require_stable_id(
            self.original_operation_id,
            "operation",
            "original_operation_id",
        )
        _require_stable_id(
            self.compensation_operation_id,
            "operation",
            "compensation_operation_id",
        )
        if self.original_operation_id == self.compensation_operation_id:
            raise ValueError("compensation operation must have an independent identity")
        if self.tenant_id is not None:
            _require_identifier(self.tenant_id, "tenant_id")
        _require_identifier(self.session_id, "session_id")
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.turn_id, "turn_id")
        _require_identifier(self.invocation_id, "invocation_id")
        _require_attempt(self.attempt)


@dataclass(frozen=True, slots=True, init=False)
class ToolCompensationInvocation:
    """补偿 handler 的唯一不可变输入。"""

    arguments: FrozenJsonObject
    context: ToolCompensationContext
    result_ref: ToolResultRef | None

    def __init__(
        self,
        arguments: dict[str, object] | FrozenJsonObject,
        context: ToolCompensationContext,
        result_ref: ToolResultRef | None,
    ) -> None:
        if type(context) is not ToolCompensationContext:
            raise TypeError("context must be ToolCompensationContext")
        if result_ref is not None and type(result_ref) not in {
            InlineToolResultRef,
            ArtifactToolResultRef,
        }:
            raise TypeError("result_ref must be a ToolResultRef or None")
        object.__setattr__(self, "arguments", freeze_json_mapping(arguments))
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "result_ref", result_ref)


def _require_identifier(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value != value.strip()
        or any(
            character.isspace()
            or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        raise ValueError(f"{field_name} must be a valid identifier")


def _require_attempt(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("attempt must be a positive integer")


def _require_stable_id(value: object, prefix: str, field_name: str) -> None:
    expected_prefix = f"{prefix}_"
    if (
        type(value) is not str
        or len(value) != len(expected_prefix) + 32
        or not value.startswith(expected_prefix)
        or any(character not in "0123456789abcdef" for character in value[len(expected_prefix):])
    ):
        raise ValueError(f"{field_name} must be a canonical {prefix} id")


__all__ = [
    "ToolCompensationContext",
    "ToolCompensationInvocation",
    "ToolInvocation",
    "ToolInvocationContext",
]
