from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Callable, Literal


CaptureMode = Literal["metadata", "redacted", "full"]
Redactor = Callable[[object], object]


_SECRET_KEY_PATTERN = re.compile(
    r"(authorization|api[_-]?key|secret|token|password)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_-]+|sk-ant-[A-Za-z0-9_-]+|pk-lf-[A-Za-z0-9_-]+)",
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def default_redactor(value: object) -> object:
    """递归替换常见 secret 值。"""

    if isinstance(value, dict):
        redacted: dict[object, object] = {}
        for key, item in value.items():
            if _SECRET_KEY_PATTERN.search(str(key)):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = default_redactor(item)
        return redacted
    if isinstance(value, list):
        return [default_redactor(item) for item in value]
    if isinstance(value, tuple):
        return tuple(default_redactor(item) for item in value)
    if isinstance(value, str):
        if _PRIVATE_KEY_PATTERN.search(value):
            return "[REDACTED]"
        return _SECRET_VALUE_PATTERN.sub("[REDACTED]", value)
    return value


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    """控制哪些 provider/tool payload 会进入观测系统。"""

    mode: CaptureMode = "metadata"
    capture_system: bool = False
    capture_messages: bool = False
    capture_tool_schemas: bool = False
    capture_provider_output: bool = False
    capture_tool_arguments: bool = False
    capture_tool_result: bool = False
    max_string_length: int = 4000
    redactor: Redactor = default_redactor

    @classmethod
    def metadata_only(cls) -> "CapturePolicy":
        """只捕获 metadata，不捕获原始 prompt/tool payload。"""

        return cls(mode="metadata")

    @classmethod
    def redacted(
        cls,
        *,
        max_string_length: int = 4000,
        redactor: Redactor = default_redactor,
    ) -> "CapturePolicy":
        """捕获经过 redaction 的 provider/tool payload。"""

        return cls(
            mode="redacted",
            capture_system=True,
            capture_messages=True,
            capture_tool_schemas=True,
            capture_provider_output=True,
            capture_tool_arguments=True,
            capture_tool_result=True,
            max_string_length=max_string_length,
            redactor=redactor,
        )

    @classmethod
    def full_for_local_development(
        cls,
        *,
        max_string_length: int = 4000,
    ) -> "CapturePolicy":
        """本地开发用完整捕获模式。"""

        return cls(
            mode="full",
            capture_system=True,
            capture_messages=True,
            capture_tool_schemas=True,
            capture_provider_output=True,
            capture_tool_arguments=True,
            capture_tool_result=True,
            max_string_length=max_string_length,
        )


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """QueryLoop instrumentation 使用的观测配置。"""

    tracer: object
    capture_policy: CapturePolicy = field(default_factory=CapturePolicy.metadata_only)


def json_attribute(value: object, *, policy: CapturePolicy) -> str:
    """把 span input/output 序列化为稳定 JSON attribute。"""

    processed = policy.redactor(value)
    processed = _limit_strings(processed, policy.max_string_length)
    return json.dumps(
        processed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _limit_strings(value: object, max_length: int) -> object:
    """递归限制字符串长度。"""

    if isinstance(value, dict):
        return {key: _limit_strings(item, max_length) for key, item in value.items()}
    if isinstance(value, list):
        return [_limit_strings(item, max_length) for item in value]
    if isinstance(value, tuple):
        return tuple(_limit_strings(item, max_length) for item in value)
    if isinstance(value, str) and len(value) > max_length:
        return value[:max_length] + "..."
    return value
