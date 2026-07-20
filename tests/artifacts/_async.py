from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar


_P = ParamSpec("_P")
_T = TypeVar("_T")


def async_test(function: Callable[_P, Awaitable[_T]]) -> Callable[_P, _T]:
    """Run one async pytest test in an isolated event loop."""

    @wraps(function)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        return asyncio.run(function(*args, **kwargs))

    return wrapper
