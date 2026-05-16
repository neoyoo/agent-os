from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import random
import time


class ProviderCircuitOpenError(RuntimeError):
    """provider circuit 已打开，当前请求快速失败。"""


@dataclass(slots=True)
class RetryPolicy:
    """Provider 调用 retry 和 circuit breaker 策略。"""

    max_retries: int = 0
    retry_on: tuple[type[BaseException], ...] = (RuntimeError,)
    backoff_base: float = 0.25
    jitter: float = 0.1
    circuit_failure_threshold: int = 0
    circuit_open_seconds: float = 30.0
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _circuit_open_until: float = field(default=0, init=False, repr=False)

    def raise_if_open(self) -> None:
        """如果 circuit 仍处于 open 状态则快速失败。"""

        if self.circuit_failure_threshold < 1:
            return
        if self.now() < self._circuit_open_until:
            raise ProviderCircuitOpenError("provider circuit is open")

    def should_retry(self, error: BaseException, attempt: int) -> bool:
        """判断当前异常和 attempt 是否允许 retry。"""

        return attempt <= self.max_retries and isinstance(error, self.retry_on)

    def delay_for_attempt(self, attempt: int) -> float:
        """计算指数 backoff + jitter 延迟。"""

        delay = self.backoff_base * (2 ** max(0, attempt - 1))
        if self.jitter:
            delay += random.uniform(0, self.jitter)
        return delay

    def record_success(self) -> None:
        """记录一次成功调用并关闭 circuit。"""

        self._consecutive_failures = 0
        self._circuit_open_until = 0

    def record_failure(self) -> None:
        """记录一次最终失败，并在达到阈值后打开 circuit。"""

        self._consecutive_failures += 1
        if (
            self.circuit_failure_threshold > 0
            and self._consecutive_failures >= self.circuit_failure_threshold
        ):
            self._circuit_open_until = self.now() + self.circuit_open_seconds
