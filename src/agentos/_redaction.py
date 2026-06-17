from __future__ import annotations

import re
from collections.abc import Sequence


_SECRET_KEY_PATTERN = re.compile(
    r"(authorization|api[_-]?key|apikey|credential|password|private[_-]?key|secret|token)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)([^\s,;]+)")
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|apikey|credential|password|private[_-]?key|secret|token)"
    r"\s*([=:])\s*([^\s,;]+)",
)
_OPENAI_STYLE_KEY_PATTERN = re.compile(
    r"\b(?:sk|pk|rk|sess|pat)-[A-Za-z0-9][A-Za-z0-9._-]{4,}\b",
)
_URL_USERINFO_PATTERN = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)"
    r"(?P<userinfo>[^/@\s:]+:[^/@\s]+)@",
)


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
    redacted = redact_secret_patterns(value)
    for separator in ("=", ":"):
        key, found, secret = redacted.partition(separator)
        if found and secret and is_secret_like_key(key):
            stripped_secret = secret.strip()
            if stripped_secret.lower().startswith(("bearer ", "basic ")):
                if "<redacted>" in stripped_secret:
                    return redacted
            return f"{key}{separator}<redacted>"
    return redacted


def redact_secret_patterns(value: str) -> str:
    """Redact common credential patterns from free-form evidence text."""

    redacted = _BEARER_PATTERN.sub(r"\1<redacted>", value)
    redacted = _INLINE_SECRET_PATTERN.sub(
        _redact_inline_secret_match,
        redacted,
    )
    redacted = _OPENAI_STYLE_KEY_PATTERN.sub("<redacted>", redacted)
    return _URL_USERINFO_PATTERN.sub(r"\g<scheme><redacted>@", redacted)


def _redact_inline_secret_match(match: re.Match[str]) -> str:
    value = match.group(3)
    following = match.string[match.end() :]
    if value.lower() in {"bearer", "basic"} and following.startswith(" <redacted>"):
        return match.group(0)
    if value == "<redacted>":
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}<redacted>"


def _is_secret_flag_without_value(value: str) -> bool:
    if not value.startswith("-") or "=" in value or ":" in value:
        return False
    return is_secret_like_key(value.lstrip("-/"))


def is_secret_like_key(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return bool(_SECRET_KEY_PATTERN.search(normalized))


def _is_secret_like_key(value: str) -> bool:
    return is_secret_like_key(value)
