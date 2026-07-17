from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import parse_qsl, urlsplit

from agentos._redaction import is_secret_like_key, redact_secret_patterns
from agentos.runtime.errors import DurableUnsafeDataError


_DATA_URL = re.compile(r"data:[^\s,;]+(?:;[^\s,]+)*;base64,", re.IGNORECASE)
_PROVIDER_FILE_ID = re.compile(r"\bfile-[A-Za-z0-9_-]{8,}\b")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\r\n\t]*")
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_RAW_BASE64 = re.compile(r"[A-Za-z0-9+/]{128,}={0,2}")
_SIGNED_QUERY_KEYS = frozenset(
    {"sig", "signature", "x-amz-signature", "x-goog-signature"}
)


def validate_durable_data(value: object) -> None:
    """递归拒绝 Durable SQLite 明确禁止的敏感表示。"""

    if value is None or type(value) in {bool, int, float}:
        return
    if type(value) is str:
        _validate_text(value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                _unsafe()
            _validate_text(key)
            validate_durable_data(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            validate_durable_data(item)
        return
    _unsafe()


def _validate_text(value: str) -> None:
    if (
        _DATA_URL.search(value)
        or _PROVIDER_FILE_ID.search(value)
        or _WINDOWS_PATH.search(value)
        or redact_secret_patterns(value) != value
    ):
        _unsafe()
    if PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute():
        _unsafe()
    for match in _URL.finditer(value):
        parsed = urlsplit(match.group(0))
        if parsed.username or parsed.password:
            _unsafe()
        for key, _item in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = key.lower()
            if is_secret_like_key(normalized) or normalized in _SIGNED_QUERY_KEYS:
                _unsafe()
    for match in _RAW_BASE64.finditer(value):
        candidate = match.group(0)
        try:
            base64.b64decode(candidate, validate=True)
        except ValueError:
            continue
        _unsafe()


def _unsafe() -> None:
    raise DurableUnsafeDataError("durable data contains a forbidden representation")


__all__ = ["validate_durable_data"]
