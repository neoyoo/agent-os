from __future__ import annotations

import copy
from collections.abc import Callable

from agentos.providers.base import ProviderRequest
from agentos.providers.input import ProviderInputItem
from agentos.providers.tool_specs import provider_tool_spec_to_dict


def build_chat_completions_payload(
    *,
    model: str,
    request: ProviderRequest,
    message_to_dict: Callable[[ProviderInputItem], dict[str, object]],
    thinking: dict[str, object] | None,
    extra_body: dict[str, object] | None,
    supports_parallel_tool_calls_parameter: bool,
) -> dict[str, object]:
    """构造 OpenAI-compatible chat completions payload。"""

    payload: dict[str, object] = copy.deepcopy(extra_body) if extra_body else {}
    payload.update(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                *[message_to_dict(message) for message in request.messages],
            ],
        },
    )
    if request.tools:
        payload["tools"] = [
            provider_tool_spec_to_dict(tool) for tool in request.tools
        ]
        if (
            supports_parallel_tool_calls_parameter
            and request.parallel_tool_calls is not None
        ):
            payload["parallel_tool_calls"] = request.parallel_tool_calls
    if thinking is not None:
        payload["thinking"] = dict(thinking)
    return payload
