from collections import UserDict
from copy import copy, deepcopy
import pickle

import pytest

from agentos._frozen_json import FrozenJsonObject, freeze_json, thaw_json


def test_freeze_json_recursively_detaches_mutable_inputs() -> None:
    source = {
        "filters": {
            "tags": ["critical", {"name": "phase2"}],
        },
        "limit": 5,
    }

    frozen = freeze_json(source)
    source["filters"]["tags"].append("mutated")  # type: ignore[index,union-attr]

    assert isinstance(frozen, FrozenJsonObject)
    filters = frozen["filters"]
    assert isinstance(filters, FrozenJsonObject)
    assert filters["tags"] == (
        "critical",
        FrozenJsonObject((("name", "phase2"),)),
    )


def test_frozen_json_object_preserves_order_and_rejects_mutation() -> None:
    frozen = freeze_json({"first": 1, "second": {"nested": True}})

    assert isinstance(frozen, FrozenJsonObject)
    assert tuple(frozen) == ("first", "second")
    assert frozen == freeze_json({"first": 1, "second": {"nested": True}})
    with pytest.raises(TypeError):
        frozen["third"] = 3  # type: ignore[index]


def test_frozen_json_object_constructor_also_freezes_nested_aliases() -> None:
    nested = ["before"]

    frozen = FrozenJsonObject((("items", nested),))
    nested.append("after")

    assert frozen["items"] == ("before",)


def test_frozen_json_object_internal_storage_cannot_be_rebound() -> None:
    frozen = FrozenJsonObject((("value", 1),))

    with pytest.raises(AttributeError):
        frozen._items = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del frozen._items  # type: ignore[misc]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (True, 1),
        (False, 0),
        (1, 1.0),
    ],
)
def test_frozen_json_equality_preserves_scalar_types(
    left: object,
    right: object,
) -> None:
    frozen = freeze_json({"value": left})

    assert frozen != freeze_json({"value": right})
    assert frozen != {"value": right}


@pytest.mark.parametrize(
    "value",
    [
        type("IntSubclass", (int,), {})(1),
        type("FloatSubclass", (float,), {})(1.0),
        type("StringSubclass", (str,), {})("value"),
        type("ListSubclass", (list,), {})([1]),
        type("TupleSubclass", (tuple,), {})((1,)),
        type("DictSubclass", (dict,), {})({"value": 1}),
    ],
)
def test_freeze_json_rejects_builtin_subclasses(value: object) -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        freeze_json(value)


def test_frozen_json_object_rejects_string_subclass_keys() -> None:
    key = type("StringSubclass", (str,), {})("key")

    with pytest.raises(TypeError, match="string keys"):
        FrozenJsonObject(((key, "value"),))


def test_frozen_json_object_cannot_be_subclassed() -> None:
    with pytest.raises(TypeError, match="cannot be subclassed"):

        class FrozenJsonSubclass(FrozenJsonObject):
            pass


def test_freeze_json_rejects_circular_values_but_allows_shared_values() -> None:
    circular_list: list[object] = []
    circular_list.append(circular_list)
    circular_dict: dict[str, object] = {}
    circular_dict["self"] = circular_dict

    with pytest.raises(ValueError, match="circular JSON value"):
        freeze_json(circular_list)
    with pytest.raises(ValueError, match="circular JSON value"):
        freeze_json(circular_dict)

    shared = {"value": [1]}
    frozen = freeze_json({"first": shared, "second": shared})
    assert thaw_json(frozen) == {
        "first": {"value": [1]},
        "second": {"value": [1]},
    }


def test_frozen_json_hash_matches_type_sensitive_equality() -> None:
    first = freeze_json({"a": [1, True], "b": {"value": 1.0}})
    same = freeze_json({"b": {"value": 1.0}, "a": [1, True]})
    different = freeze_json({"a": [1, 1], "b": {"value": 1.0}})

    assert first == same
    assert hash(first) == hash(same)
    assert first != different
    assert len({first, same, different}) == 2


def test_frozen_json_supports_standard_immutable_value_protocols() -> None:
    frozen = freeze_json({"nested": {"items": [1, True]}})

    assert copy(frozen) is frozen
    assert deepcopy(frozen) is frozen
    restored = pickle.loads(pickle.dumps(frozen))
    assert type(restored) is FrozenJsonObject
    assert restored == frozen
    assert hash(restored) == hash(frozen)


def test_freeze_json_rejects_values_outside_the_json_closed_set() -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        freeze_json({"bad": object()})
    with pytest.raises(TypeError, match="string keys"):
        freeze_json({1: "bad"})
    with pytest.raises(TypeError, match="dict"):
        freeze_json(UserDict({"unsupported": True}))
    with pytest.raises(ValueError, match="finite"):
        freeze_json(float("nan"))
    with pytest.raises(ValueError, match="finite"):
        freeze_json(float("inf"))


def test_thaw_json_returns_fresh_mutable_wire_values() -> None:
    frozen = freeze_json({"items": [{"name": "drawing.png"}]})

    first = thaw_json(frozen)
    second = thaw_json(frozen)

    assert first == {"items": [{"name": "drawing.png"}]}
    assert second == first
    assert first is not second
    first["items"][0]["name"] = "changed.png"  # type: ignore[index]
    assert second == {"items": [{"name": "drawing.png"}]}
