from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class ChannelAuthError(PermissionError):
    """Channel 请求未通过鉴权。"""


class ChannelAuthPolicy(Protocol):
    """Channel 层鉴权边界。"""

    def authorize(self, headers: Mapping[str, str]) -> None:
        """校验请求 headers，不通过时抛出 ChannelAuthError。"""


class AllowAllChannelAuthPolicy:
    """默认 local/dev 鉴权策略，不拒绝任何请求。"""

    def authorize(self, headers: Mapping[str, str]) -> None:
        """允许所有请求。"""
