from collections.abc import Iterator
from dataclasses import dataclass, field

from agentos.providers.base import ProviderRequest, ProviderResponse
from agentos.providers.stream import (
    ProviderStreamEvent,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)


@dataclass(slots=True)
class FakeProvider:
    """用于测试 runtime loop 的确定性 provider。"""

    responses: list[str | ProviderResponse]
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """记录请求并返回下一个预设响应。"""

        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("FakeProvider has no responses left")
        response = self.responses.pop(0)
        if isinstance(response, ProviderResponse):
            return response
        return ProviderResponse(content=response, stop_reason="stop")

    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """把预设完整响应适配为 provider stream events。"""

        request_id = f"fake_provider_{len(self.requests) + 1}"
        response = self.complete(request)
        yield from complete_response_to_stream_events(
            request_id=request_id,
            response=response,
            options=options,
        )
