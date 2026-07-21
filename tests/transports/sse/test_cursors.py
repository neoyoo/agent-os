from __future__ import annotations

import base64
import json

import pytest

from agentos.transports.sse.cursors import (
    SseCursorError,
    decode_cursor,
    encode_cursor,
)


def test_scoped_cursor_is_deterministic_canonical_json() -> None:
    token = encode_cursor(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        position="123-0",
    )
    padded = token + "=" * (-len(token) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))

    assert token == (
        "eyJwb3NpdGlvbiI6IjEyMy0wIiwic2NvcGUiOiI1NjI4MjQwYmFkMWI1YTZiZWFiZGM4"
        "OGYzN2QyOTIyNTk3NDNmYTM5MjE2Yzg5YzBhNTRkNTg1ZjhjNzI2NDRiIiwidmVyc2lv"
        "biI6MX0"
    )
    assert token == encode_cursor(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        position="123-0",
    )
    assert "=" not in token
    assert list(payload) == ["position", "scope", "version"]
    assert payload["position"] == "123-0"
    assert len(payload["scope"]) == 64
    assert payload["version"] == 1
    assert decode_cursor(
        token,
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
    ) == "123-0"


@pytest.mark.parametrize(
    ("tenant_id", "session_id", "run_id"),
    [
        ("tenant_2", "session_1", "run_1"),
        ("tenant_1", "session_2", "run_1"),
        ("tenant_1", "session_1", "run_2"),
    ],
)
def test_cursor_cannot_cross_authenticated_scope(
    tenant_id: str,
    session_id: str,
    run_id: str,
) -> None:
    token = encode_cursor(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        position="123-0",
    )

    with pytest.raises(SseCursorError, match="^invalid SSE cursor$"):
        decode_cursor(
            token,
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
        )


@pytest.mark.parametrize(
    "token",
    [
        "not-base64!",
        "abc=",
        "é",
        "a" * 1025,
    ],
)
def test_cursor_rejects_invalid_or_oversized_token(token: str) -> None:
    with pytest.raises(SseCursorError, match="^invalid SSE cursor$"):
        decode_cursor(
            token,
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
        )


def test_cursor_rejects_noncanonical_json_version_and_position() -> None:
    def token(payload: bytes) -> str:
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    values = [
        b'{"scope":"x","position":"1-0","version":1}',
        b'{"position":"01-0","scope":"x","version":1}',
        b'{"position":"1-0","scope":"x","version":2}',
        b'{"position":"1-0","scope":"x","version":1,"extra":true}',
        b'{"position":"1-0","position":"2-0","scope":"x","version":1}',
    ]

    for value in values:
        with pytest.raises(SseCursorError, match="^invalid SSE cursor$"):
            decode_cursor(
                token(value),
                tenant_id="tenant_1",
                session_id="session_1",
                run_id="run_1",
            )


@pytest.mark.parametrize("scope_part", ["scope part", "scope\x00part", "s" * 256])
def test_cursor_rejects_invalid_scope_identifier(scope_part: str) -> None:
    with pytest.raises(SseCursorError, match="^invalid SSE cursor$"):
        encode_cursor(
            tenant_id=scope_part,
            session_id="session_1",
            run_id="run_1",
            position="1-0",
        )
