from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise AssertionError(
            f"{message}: expected {expected!r}, got {actual!r}",
        )


def check_is(actual: object, expected: object, message: str) -> None:
    if actual is not expected:
        raise AssertionError(
            f"{message}: expected {expected!r}, got {actual!r}",
        )


def check_in(member: object, container: object, message: str) -> None:
    try:
        present = member in container  # type: ignore[operator]
    except TypeError as error:
        raise AssertionError(f"{message}: container is not searchable") from error
    if not present:
        raise AssertionError(f"{message}: {member!r} not found in {container!r}")


def require_not_none(value: T | None, message: str) -> T:
    if value is None:
        raise AssertionError(message)
    return value


def require_callable(value: object, message: str) -> Callable[..., Any]:
    if not callable(value):
        raise AssertionError(message)
    return value
