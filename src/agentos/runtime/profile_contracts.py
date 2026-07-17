from __future__ import annotations

from typing import Protocol

from agentos.runtime.agent import Agent


class RuntimeProfile(Protocol):
    """部署形态装配边界，不负责执行 turn。"""

    name: str

    def build_agent(self, session_id: str | None = None) -> Agent:
        """构建当前 profile 下的 Agent。"""


class ChannelRuntimeProfile(RuntimeProfile, Protocol):
    """可暴露 channel app 的 runtime profile。"""

    def build_channel_app(self) -> object:
        """构建 ASGI 或其他 channel app。"""


class DistributedRuntimeProfile(ChannelRuntimeProfile, Protocol):
    """包含分布式 session、registry 或 task 边界的 runtime profile。"""

    def readiness_checks(self) -> dict[str, object]:
        """返回可接入 readiness endpoint 的检查项。"""


class RuntimeCompositionProfile(Protocol):
    """只组装 runtime 边界、不构建单个 Agent 的 Profile。"""

    name: str

    def readiness_checks(self) -> dict[str, object]:
        """返回 Profile 级 readiness checks。"""
