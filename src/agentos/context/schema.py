from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from agentos.context.models import ContextProtocolError


WORKING_STATE_FIELD_TYPES = frozenset(
    {
        "string",
        "integer",
        "number",
        "boolean",
        "null",
        "list[string]",
        "list[integer]",
        "list[number]",
        "list[boolean]",
        "list[object]",
        "object",
    },
)

_FIELD_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_VALUE_TYPE_ERROR = "working state field value does not match declared type"
_JSON_VALUE_ERROR = "working state field value must be recursively JSON-compatible"


@dataclass(frozen=True, slots=True)
class WorkingStateField:
    """LLM 可见 working state schema 中的字段声明。"""

    name: str
    type: str
    purpose: str


@dataclass(frozen=True, slots=True)
class WorkingStateSchema:
    """当前 chapter 内锁定的 working state schema。"""

    fields: tuple[WorkingStateField, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """把字段序列冻结为 tuple，避免外部绕过 schema 锁定。"""

        object.__setattr__(self, "fields", tuple(self.fields))


def validate_working_state_fields(
    fields: object,
    *,
    allow_empty: bool = False,
) -> tuple[WorkingStateField, ...]:
    """严格校验协议字段，并按声明顺序返回不可变序列。"""

    if not isinstance(fields, (list, tuple)):
        raise ContextProtocolError("schema declaration fields must be a sequence")
    normalized = tuple(fields)
    if not normalized and not allow_empty:
        raise ContextProtocolError("schema declaration requires at least one field")

    seen: set[str] = set()
    for item in normalized:
        _validate_working_state_field(item)
        if item.name in seen:
            raise ContextProtocolError("duplicate field in schema declaration")
        seen.add(item.name)
    return normalized


def validate_working_state_value(field_type: str, value: object) -> None:
    """按声明的 Context Protocol 类型严格校验字段值。"""

    if type(field_type) is not str or field_type not in WORKING_STATE_FIELD_TYPES:
        raise ContextProtocolError("working state field type is invalid")
    if field_type.startswith("list["):
        _validate_list_value(field_type[5:-1], value)
        return
    if field_type == "object":
        if not isinstance(value, Mapping):
            raise ContextProtocolError(_VALUE_TYPE_ERROR)
        _validate_json_value(value, set())
        return
    if field_type == "number":
        _validate_number(value)
        return

    expected_types = {
        "string": str,
        "integer": int,
        "boolean": bool,
    }
    if field_type == "null":
        valid = value is None
    else:
        valid = type(value) is expected_types[field_type]
    if not valid:
        raise ContextProtocolError(_VALUE_TYPE_ERROR)


def json_compatible_value(value: object) -> object:
    """校验并复制 object 值为标准库 JSON 可序列化结构。"""

    _validate_json_value(value, set())
    return _copy_json_value(value)


def _validate_working_state_field(item: object) -> None:
    if type(item) is not WorkingStateField:
        raise ContextProtocolError("working state field must be WorkingStateField")
    if type(item.name) is not str or _FIELD_NAME_PATTERN.fullmatch(item.name) is None:
        raise ContextProtocolError("working state field name is invalid")
    if type(item.type) is not str or item.type not in WORKING_STATE_FIELD_TYPES:
        raise ContextProtocolError("working state field type is invalid")
    if type(item.purpose) is not str or len(item.purpose) > 300:
        raise ContextProtocolError("working state field purpose is invalid")


def _validate_list_value(item_type: str, value: object) -> None:
    if not isinstance(value, (list, tuple)):
        raise ContextProtocolError(_VALUE_TYPE_ERROR)
    for item in value:
        if item_type == "object":
            if not isinstance(item, Mapping):
                raise ContextProtocolError(_VALUE_TYPE_ERROR)
            _validate_json_value(item, set())
        else:
            validate_working_state_value(item_type, item)


def _validate_number(value: object) -> None:
    if type(value) not in (int, float):
        raise ContextProtocolError(_VALUE_TYPE_ERROR)
    if type(value) is float and not math.isfinite(value):
        raise ContextProtocolError("working state field value numbers must be finite")


def _validate_json_value(value: object, ancestors: set[int]) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ContextProtocolError(
                "working state field value numbers must be finite",
            )
        return
    if isinstance(value, Mapping):
        _validate_json_mapping(value, ancestors)
        return
    if isinstance(value, (list, tuple)):
        _validate_json_sequence(value, ancestors)
        return
    raise ContextProtocolError(_JSON_VALUE_ERROR)


def _validate_json_mapping(
    value: Mapping[object, object],
    ancestors: set[int],
) -> None:
    identity = id(value)
    if identity in ancestors:
        raise ContextProtocolError(_JSON_VALUE_ERROR)
    ancestors.add(identity)
    try:
        for key, item in value.items():
            if type(key) is not str:
                raise ContextProtocolError(_JSON_VALUE_ERROR)
            _validate_json_value(item, ancestors)
    finally:
        ancestors.remove(identity)


def _validate_json_sequence(
    value: list[object] | tuple[object, ...],
    ancestors: set[int],
) -> None:
    identity = id(value)
    if identity in ancestors:
        raise ContextProtocolError(_JSON_VALUE_ERROR)
    ancestors.add(identity)
    try:
        for item in value:
            _validate_json_value(item, ancestors)
    finally:
        ancestors.remove(identity)


def _copy_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_json_value(item) for item in value]
    return value
