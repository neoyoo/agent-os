from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from math import isfinite
from typing import TypeAlias


JsonScalar: TypeAlias = None | bool | int | float | str


class FrozenJsonObject(Mapping[str, "FrozenJsonValue"]):
    """只读、保持插入顺序且可比较、可哈希的 JSON object。"""

    __slots__ = ("_items",)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("FrozenJsonObject cannot be subclassed")

    def __init__(self, items: Iterable[tuple[str, object]] = ()) -> None:
        raw_items = tuple(items)
        keys = tuple(key for key, _ in raw_items)
        if any(type(key) is not str for key in keys):
            raise TypeError("JSON object requires string keys")
        if len(set(keys)) != len(keys):
            raise ValueError("JSON object keys must be unique")
        active: set[int] = set()
        object.__setattr__(
            self,
            "_items",
            tuple((key, _freeze_json_value(value, active)) for key, value in raw_items),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

    def __getitem__(self, key: str) -> FrozenJsonValue:
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
            frozen = freeze_json_value(other)
        except (TypeError, ValueError):
            return False
        return isinstance(frozen, FrozenJsonObject) and _json_values_equal(self, frozen)

    def __hash__(self) -> int:
        return _json_value_hash(self)

    def __copy__(self) -> FrozenJsonObject:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenJsonObject:
        memo[id(self)] = self
        return self

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        return FrozenJsonObject, (self._items,)

    def __repr__(self) -> str:
        return f"FrozenJsonObject({self._items!r})"


FrozenJsonValue: TypeAlias = (
    JsonScalar | tuple["FrozenJsonValue", ...] | FrozenJsonObject
)
JsonValue: TypeAlias = FrozenJsonValue


def _json_values_equal(left: FrozenJsonValue, right: FrozenJsonValue) -> bool:
    if type(left) is FrozenJsonObject:
        return (
            type(right) is FrozenJsonObject
            and len(left) == len(right)
            and all(
                key in right and _json_values_equal(value, right[key])
                for key, value in left.items()
            )
        )
    if type(left) is tuple:
        return (
            type(right) is tuple
            and len(left) == len(right)
            and all(
                _json_values_equal(a, b)
                for a, b in zip(left, right, strict=True)
            )
        )
    return type(left) is type(right) and left == right


def _json_value_hash(value: FrozenJsonValue) -> int:
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


def freeze_json_value(value: object) -> FrozenJsonValue:
    """递归复制并冻结 JSON-compatible 值。"""

    return _freeze_json_value(value, set())


def freeze_json_mapping(value: Mapping[str, object]) -> FrozenJsonObject:
    """递归复制并冻结 JSON object。"""

    if isinstance(value, FrozenJsonObject):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("JSON object requires a mapping")
    frozen = freeze_json_value(dict(value))
    if not isinstance(frozen, FrozenJsonObject):
        raise TypeError("JSON object requires a mapping")
    return frozen


def _freeze_json_value(value: object, active: set[int]) -> FrozenJsonValue:
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
        return _freeze_sequence(value, active)
    if value_type is dict:
        return _freeze_object(value, active)
    if isinstance(value, Mapping):
        raise TypeError("JSON-compatible objects must use dict")
    raise TypeError("value must be JSON-compatible")


def _freeze_sequence(
    value: list[object] | tuple[object, ...],
    active: set[int],
) -> tuple[FrozenJsonValue, ...]:
    container_id = id(value)
    if container_id in active:
        raise ValueError("circular JSON value")
    active.add(container_id)
    try:
        return tuple(_freeze_json_value(item, active) for item in value)
    finally:
        active.remove(container_id)


def _freeze_object(
    value: dict[str, object],
    active: set[int],
) -> FrozenJsonObject:
    if any(type(key) is not str for key in value):
        raise TypeError("JSON object requires string keys")
    container_id = id(value)
    if container_id in active:
        raise ValueError("circular JSON value")
    active.add(container_id)
    try:
        items = tuple(
            (key, _freeze_json_value(item, active))
            for key, item in value.items()
        )
    finally:
        active.remove(container_id)
    frozen = object.__new__(FrozenJsonObject)
    object.__setattr__(frozen, "_items", items)
    return frozen


def thaw_json_value(value: FrozenJsonValue) -> object:
    """把冻结值递归复制为新的 JSON wire dict/list。"""

    value_type = type(value)
    if value is None or value_type in (bool, int, float, str):
        return value
    if value_type is tuple:
        return [thaw_json_value(item) for item in value]
    if value_type is FrozenJsonObject:
        return {key: thaw_json_value(item) for key, item in value.items()}
    raise TypeError("value is not a frozen JSON value")


freeze_json = freeze_json_value
thaw_json = thaw_json_value
