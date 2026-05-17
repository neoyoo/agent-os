from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """限流判断结果。"""

    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    """Channel 层可注入限流器边界。"""

    def check(self, key: str) -> RateLimitDecision:
        """返回指定 key 是否允许进入处理路径。"""


class SlidingWindowRateLimiter:
    """基于内存滑动窗口的 per-key RPM 限流器。"""

    def __init__(
        self,
        *,
        max_requests: int = 60,
        window_seconds: float = 60,
        now: Callable[[], float] = time.time,
    ) -> None:
        """创建限流器。"""

        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._now = now
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> RateLimitDecision:
        """记录当前请求并返回是否允许。"""

        now = self._now()
        window_start = now - self._window_seconds
        self._evict(window_start)
        bucket = self._requests[key]
        while bucket and bucket[0] <= window_start:
            bucket.popleft()
        if len(bucket) >= self._max_requests:
            retry_after = max(1, int(bucket[0] + self._window_seconds - now))
            return RateLimitDecision(False, retry_after)
        bucket.append(now)
        return RateLimitDecision(True, 0)

    def _evict(self, window_start: float) -> None:
        """清理已经没有窗口内请求的 session bucket。"""

        for key, bucket in list(self._requests.items()):
            while bucket and bucket[0] <= window_start:
                bucket.popleft()
            if not bucket:
                del self._requests[key]
