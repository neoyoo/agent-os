"""同步与异步 Provider attempt 共用的纯状态语义。"""

from dataclasses import dataclass

from agentos.providers import (
    ProviderContentDelta,
    ProviderResponse,
    ProviderStreamCancelled,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamFailed,
    ProviderStreamOptions,
    ProviderThinkingDelta,
)
from agentos.runtime.retry import RetryPolicy


@dataclass(slots=True)
class ProviderAttemptState:
    """跟踪单个物理 attempt 的完成、可见输出和 retry 阶段。"""

    options: ProviderStreamOptions | None
    completion: ProviderStreamCompleted | None = None
    visible_delta_emitted: bool = False
    retryable_error: bool = False

    def enter_provider_call(self) -> None:
        """标记后续异常属于 Provider 调用边界。"""

        self.retryable_error = True

    def accept_stream_event(self, event: ProviderStreamEvent) -> bool:
        """处理 Provider event；返回是否应继续向上游转发。"""

        if self.completion is not None:
            self.retryable_error = False
            raise RuntimeError("provider stream emitted an event after completion")
        if isinstance(event, ProviderStreamCompleted):
            self.completion = event
            return False
        if isinstance(event, ProviderStreamFailed):
            raise event.error
        if isinstance(event, ProviderStreamCancelled):
            raise RuntimeError(event.reason or "provider stream was cancelled")
        if isinstance(event, ProviderContentDelta) or (
            isinstance(event, ProviderThinkingDelta)
            and self.options is not None
            and self.options.show_thinking
        ):
            self.visible_delta_emitted = True
        return True

    def require_completion(self) -> ProviderStreamCompleted:
        """返回原始 completion；缺失时产生稳定错误。"""

        if self.completion is None:
            raise RuntimeError("provider stream ended without completion event")
        return self.completion

    def enter_after_hook(self) -> None:
        """after hook 失败不属于 Provider retry/circuit。"""

        self.retryable_error = False

    def enter_validation(self) -> None:
        """响应可用性失败仍属于 Provider 响应失败。"""

        self.retryable_error = True

    def enter_consumption(self) -> None:
        """receipt consumption 失败不属于 Provider retry/circuit。"""

        self.retryable_error = False

    def should_retry(
        self,
        policy: RetryPolicy | None,
        error: Exception,
        attempt: int,
    ) -> bool:
        """按阶段、可见输出和 RetryPolicy 判断是否重试。"""

        return (
            self.retryable_error
            and not self.visible_delta_emitted
            and policy is not None
            and policy.should_retry(error, attempt)
        )

    def record_terminal_failure(self, policy: RetryPolicy | None) -> None:
        """只把 Provider 边界的最终失败计入 circuit。"""

        if policy is not None and self.retryable_error:
            policy.record_failure()

    def completed_event(self, response: ProviderResponse) -> ProviderStreamCompleted:
        """使用 after-hook 后响应构造一致的最终 completion。"""

        completion = self.require_completion()
        return ProviderStreamCompleted(
            request_id=completion.request_id,
            response=response,
            stop_reason=response.stop_reason,
        )
