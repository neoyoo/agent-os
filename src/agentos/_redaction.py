from __future__ import annotations

import re
from collections.abc import Sequence


_SECRET_KEY_PATTERN = re.compile(
    r"(authorization|api[_-]?key|apikey|credential|password|private[_-]?key|secret|token)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)([^\s,;]+)")


def redact_command_argv(command: Sequence[str]) -> tuple[str, ...]:
    """Return an evidence-safe argv tuple without mutating the executable command."""

    redacted: list[str] = []
    redact_next = False
    for item in command:
        value = str(item)
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        redacted.append(_redact_inline_command_value(value))
        redact_next = _is_secret_flag_without_value(value)
    return tuple(redacted)


def _redact_inline_command_value(value: str) -> str:
    redacted = _BEARER_PATTERN.sub(r"\1<redacted>", value)
    if redacted != value:
        return redacted
    for separator in ("=", ":"):
        key, found, secret = redacted.partition(separator)
        if found and secret and is_secret_like_key(key):
            return f"{key}{separator}<redacted>"
    return redacted


def _is_secret_flag_without_value(value: str) -> bool:
    if not value.startswith("-") or "=" in value or ":" in value:
        return False
    return is_secret_like_key(value.lstrip("-/"))


def is_secret_like_key(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return bool(_SECRET_KEY_PATTERN.search(normalized))


def _is_secret_like_key(value: str) -> bool:
    return is_secret_like_key(value)
