"""Distributed runtime contracts and high-level composition."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.distributed.profile import DistributedRuntimeProfile


__all__ = ["DistributedRuntimeProfile"]


def __getattr__(name: str) -> object:
    if name != "DistributedRuntimeProfile":
        raise AttributeError(name)
    from agentos.distributed.profile import DistributedRuntimeProfile

    return DistributedRuntimeProfile
