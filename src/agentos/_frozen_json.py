from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from math import isfinite
from typing import TypeAlias


JsonScalar: TypeAlias = None | bool | int | float | str


class FrozenJsonObject(Mapping[str, "JsonValue"]):
    """只读、保持插入顺序且可比较、可哈希的 JSON object。"""

    __slots__ = ("_items",)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("FrozenJsonObject cannot be subclassed")

    def __init__(
        self,
        items: Iterable[tuple[str, object]] = (),
    ) -> None:
        raw_items = tuple(items)
        keys = tuple(key for key, _ in raw_items)
        if any(type(key) is not str for key in keys):
            raise TypeError("JSON object requires string keys")
        if len(set(keys)) != len(keys):
            raise ValueError("JSON object keys must be unique")
        active_container_ids: set[int] = set()
        object.__setattr__(
            self,
            "_items",
            tuple(
                (key, _freeze_json(value, active_container_ids))
                for key, value in raw_items
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

    def __getitem__(self, key: str) -> JsonValue:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenJsonObject):
            return _json_values_equal(self, other)
        if type(other) is not dict:
            return NotImplemented
        try:
            frozen_other = freeze_json(other)
        except (TypeError, ValueError):
            return False
        return (
            isinstance(frozen_other, FrozenJsonObject)
            and _json_values_equal(self, frozen_other)
        )

    def __hash__(self) -> int:
        return _json_value_hash(self)

    def __copy__(self) -> "FrozenJsonObject":
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> "FrozenJsonObject":
        memo[id(self)] = self
        return self

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        return FrozenJsonObject, (self._items,)

    def __repr__(self) -> str:
        return f"FrozenJsonObject({self._items!r})"


JsonValue: TypeAlias = (
    JsonScalar | tuple["JsonValue", ...] | FrozenJsonObject
)


def _json_values_equal(left: JsonValue, right: JsonValue) -> bool:
    if type(left) is FrozenJsonObject:
        if type(right) is not FrozenJsonObject or len(left) != len(right):
            return False
        return all(
            key in right and _json_values_equal(value, right[key])
            for key, value in left.items()
        )
    if type(left) is tuple:
        return (
            type(right) is tuple
            and len(left) == len(right)
            and all(
                _json_values_equal(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
        )
    return type(left) is type(right) and left == right


def _json_value_hash(value: JsonValue) -> int:
    if type(value) is FrozenJsonObject:
        return hash(
            (
                "object",
                tuple(
                    sorted(
                        (key, _json_value_hash(item))
                        for key, item in value.items()
                    ),
                ),
            ),
        )
    if type(value) is tuple:
        return hash(("array", tuple(_json_value_hash(item) for item in value)))
    if value is None:
        return hash(("null", None))
    return hash((type(value).__name__, value))


def freeze_json(value: object) -> JsonValue:
    """递归复制并冻结 JSON-compatible 值。"""

    return _freeze_json(value, set())


def _freeze_json(value: object, active_container_ids: set[int]) -> JsonValue:
    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return value
    if value_type is float:
        if not isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if value_type is FrozenJsonObject:
        return value
    if value_type in (list, tuple):
        return _freeze_json_sequence(value, active_container_ids)
    if value_type is dict:
        return _freeze_json_object(value, active_container_ids)
    if isinstance(value, Mapping):
        raise TypeError("JSON-compatible objects must use dict")
    raise TypeError(
        "value must be JSON-compatible: "
        "None, bool, int, finite float, str, list, tuple, or dict",
    )


def _freeze_json_sequence(
    value: list[object] | tuple[object, ...],
    active_container_ids: set[int],
) -> tuple[JsonValue, ...]:
    container_id = id(value)
    if container_id in active_container_ids:
        raise ValueError("circular JSON value")
    active_container_ids.add(container_id)
    try:
        return tuple(
            _freeze_json(item, active_container_ids)
            for item in value
        )
    finally:
        active_container_ids.remove(container_id)


def _freeze_json_object(
    value: dict[str, object],
    active_container_ids: set[int],
) -> FrozenJsonObject:
    if any(type(key) is not str for key in value):
        raise TypeError("JSON object requires string keys")
    container_id = id(value)
    if container_id in active_container_ids:
        raise ValueError("circular JSON value")
    active_container_ids.add(container_id)
    try:
        items = tuple(
            (key, _freeze_json(item, active_container_ids))
            for key, item in value.items()
        )
    finally:
        active_container_ids.remove(container_id)
    frozen = object.__new__(FrozenJsonObject)
    object.__setattr__(frozen, "_items", items)
    return frozen


def thaw_json(value: JsonValue) -> object:
    """把冻结值复制为新的 JSON wire dict/list。"""

    value_type = type(value)
    if value is None or value_type in (bool, int, float, str):
        return value
    if value_type is tuple:
        return [thaw_json(item) for item in value]
    if value_type is FrozenJsonObject:
        return {key: thaw_json(item) for key, item in value.items()}
    raise TypeError("value is not a frozen JSON value")
