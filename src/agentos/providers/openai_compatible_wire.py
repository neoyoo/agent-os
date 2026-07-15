"""OpenAI-compatible Chat Completions 差异化 wire 映射。"""

import copy

from agentos.providers.base import ProviderRequest
from agentos.providers.openai_chat_wire import build_openai_chat_payload


def build_openai_compatible_payload(
    *,
    model: str,
    request: ProviderRequest,
    thinking: dict[str, object] | None,
    extra_body: dict[str, object] | None,
    supports_parallel_tool_calls_parameter: bool,
    invalid_message_error: type[Exception],
) -> dict[str, object]:
    """构造带服务差异配置的 OpenAI-compatible payload。"""

    payload: dict[str, object] = copy.deepcopy(extra_body) if extra_body else {}
    payload.update(
        build_openai_chat_payload(
            model=model,
            request=request,
            include_parallel_tool_calls=supports_parallel_tool_calls_parameter,
            invalid_message_error=invalid_message_error,
        ),
    )
    if thinking is not None:
        payload["thinking"] = dict(thinking)
    return payload
