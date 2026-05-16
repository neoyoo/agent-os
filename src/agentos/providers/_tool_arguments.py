from __future__ import annotations

import json


def parse_json_object_arguments(
    arguments: str,
    *,
    provider_name: str,
) -> dict[str, object]:
    """解析 provider function-call arguments，并要求结果是 JSON object。"""

    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{provider_name} tool arguments must be valid JSON",
        ) from error
    if not isinstance(parsed, dict):
        raise ValueError(
            f"{provider_name} tool arguments must decode to an object",
        )
    return parsed


def require_tool_call_id(value: object, *, provider_name: str) -> str:
    """校验 provider tool_call id，避免 None 被静默字符串化。"""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{provider_name} tool_call requires id")
    return value


def require_tool_call_name(value: object, *, provider_name: str) -> str:
    """校验 provider function name，避免 None 被静默字符串化。"""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{provider_name} tool_call requires function name")
    return value
