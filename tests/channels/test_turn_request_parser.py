from __future__ import annotations

import pytest

from agentos.channels.types import parse_channel_turn_request


def test_parse_channel_turn_request_parses_valid_json_body() -> None:
    request = parse_channel_turn_request(
        b'{"message":"hello","thinking":true,"show_thinking":false}',
    )

    assert request.message == "hello"
    assert request.thinking is True
    assert request.show_thinking is False


def test_parse_channel_turn_request_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid JSON request body"):
        parse_channel_turn_request(b"{")


def test_parse_channel_turn_request_rejects_missing_message() -> None:
    with pytest.raises(ValueError, match="message is required"):
        parse_channel_turn_request(b'{"thinking":true}')
