from __future__ import annotations

import base64
import binascii
from hashlib import sha256
import hmac
import json
import re
from typing import Any
import unicodedata


MAX_CURSOR_BYTES = 1024
CURSOR_VERSION = 1

_TOKEN = re.compile(r"[A-Za-z0-9_-]+")
_POSITION = re.compile(r"(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)")
_SCOPE = re.compile(r"[0-9a-f]{64}")


class SseCursorError(ValueError):
    """公开 SSE cursor 不满足 canonical scoped contract。"""

    def __init__(self) -> None:
        super().__init__("invalid SSE cursor")


def encode_cursor(
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
    position: str,
) -> str:
    """把 Redis position 编码为 tenant/session/run-scoped cursor。"""

    _validate_scope_parts(tenant_id, session_id, run_id)
    _validate_position(position)
    payload = {
        "position": position,
        "scope": _scope_digest(tenant_id, session_id, run_id),
        "version": CURSOR_VERSION,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("ascii")
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(token.encode("ascii")) > MAX_CURSOR_BYTES:
        raise SseCursorError()
    return token


def decode_cursor(
    token: str,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
) -> str:
    """校验 canonical cursor 与当前已鉴权 scope，并返回 Redis position。"""

    _validate_scope_parts(tenant_id, session_id, run_id)
    if (
        type(token) is not str
        or not token
        or not token.isascii()
        or len(token.encode("ascii")) > MAX_CURSOR_BYTES
        or _TOKEN.fullmatch(token) is None
    ):
        raise SseCursorError()
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise SseCursorError() from None
    if type(payload) is not dict or set(payload) != {"position", "scope", "version"}:
        raise SseCursorError()
    position = payload["position"]
    scope = payload["scope"]
    version = payload["version"]
    if (
        type(position) is not str
        or type(scope) is not str
        or type(version) is not int
        or version != CURSOR_VERSION
        or _SCOPE.fullmatch(scope) is None
    ):
        raise SseCursorError()
    _validate_position(position)
    expected_scope = _scope_digest(tenant_id, session_id, run_id)
    if not hmac.compare_digest(scope, expected_scope):
        raise SseCursorError()
    if encode_cursor(
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
        position=position,
    ) != token:
        raise SseCursorError()
    return position


def _scope_digest(tenant_id: str, session_id: str, run_id: str) -> str:
    return sha256(f"{tenant_id}\n{session_id}\n{run_id}".encode("utf-8")).hexdigest()


def _validate_scope_parts(tenant_id: str, session_id: str, run_id: str) -> None:
    if any(
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value != value.strip()
        or any(
            char.isspace() or unicodedata.category(char).startswith("C")
            for char in value
        )
        for value in (tenant_id, session_id, run_id)
    ):
        raise SseCursorError()


def _validate_position(position: str) -> None:
    if type(position) is not str or _POSITION.fullmatch(position) is None:
        raise SseCursorError()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate cursor key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid cursor number")


__all__ = [
    "CURSOR_VERSION",
    "MAX_CURSOR_BYTES",
    "SseCursorError",
    "decode_cursor",
    "encode_cursor",
]
