"""Runtime Profile 的稳定导入门面。"""

from agentos.runtime.profile_contracts import (
    RuntimeProfile as RuntimeProfile,
)
from agentos.runtime.profile_local import LocalRuntimeProfile as LocalRuntimeProfile


__all__ = [
    "LocalRuntimeProfile",
    "RuntimeProfile",
]
