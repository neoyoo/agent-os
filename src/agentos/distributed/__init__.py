"""Distributed runtime contracts and high-level composition."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.distributed.profile import DistributedRuntimeProfile
    from agentos.distributed.worker.supervisor import DistributedWorker


__all__ = ["DistributedRuntimeProfile", "DistributedWorker"]


def __getattr__(name: str) -> object:
    if name == "DistributedRuntimeProfile":
        from agentos.distributed.profile import DistributedRuntimeProfile

        return DistributedRuntimeProfile
    if name == "DistributedWorker":
        from agentos.distributed.worker.supervisor import DistributedWorker

        return DistributedWorker
    raise AttributeError(name)
