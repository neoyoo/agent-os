from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from agentos._redaction import redact_secret_patterns


_SECRET_KEY_PARTS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    },
)


def _redact_with_findings(
    value: object,
    *,
    path: str = "",
    key_hint: str = "",
) -> tuple[object, list[str]]:
    if _is_secret_like_key(key_hint):
        return "<redacted>", (
            [f"secret-like value at {path}"] if _has_unredacted_value(value) else []
        )
    if isinstance(value, str):
        redacted = redact_secret_patterns(value)
        return redacted, (
            [f"secret-like value at {path}"] if redacted != value else []
        )
    if value is None or isinstance(value, int | float | bool):
        return value, []
    if isinstance(value, Path):
        return str(value), []
    if isinstance(value, Mapping):
        findings: list[str] = []
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            child_value, child_findings = _redact_with_findings(
                item,
                path=child_path,
                key_hint=key_text,
            )
            redacted[key_text] = child_value
            findings.extend(child_findings)
        return redacted, findings
    if isinstance(value, tuple | list):
        findings = []
        redacted_items = []
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]" if path else f"[{index}]"
            child_value, child_findings = _redact_with_findings(
                item,
                path=child_path,
            )
            redacted_items.append(child_value)
            findings.extend(child_findings)
        return tuple(redacted_items), findings
    return repr(value), []


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    safe, _ = _redact_with_findings(values)
    if isinstance(safe, Mapping):
        return dict(safe)
    return {"value": safe}


def _is_secret_like_key(key: str) -> bool:
    lowered = key.lower()
    parts = {
        part
        for part in lowered.replace("-", "_").replace(".", "_").split("_")
        if part
    }
    if lowered in _SECRET_KEY_PARTS:
        return True
    if parts & _SECRET_KEY_PARTS:
        return True
    return ("api" in parts and "key" in parts) or (
        "private" in parts and "key" in parts
    )


def _has_unredacted_value(value: object) -> bool:
    if value in (None, "", "<redacted>"):
        return False
    if isinstance(value, Mapping):
        return any(_has_unredacted_value(item) for item in value.values())
    if isinstance(value, tuple | list):
        return any(_has_unredacted_value(item) for item in value)
    return True


__all__ = [
    "_json_safe_mapping",
    "_redact_with_findings",
]

