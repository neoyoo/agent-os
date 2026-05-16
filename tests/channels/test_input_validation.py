from __future__ import annotations

import pytest

from agentos.channels.types import parse_channel_turn_request


def test_channel_turn_request_rejects_messages_over_limit() -> None:
    with pytest.raises(ValueError, match="message exceeds maximum length"):
        parse_channel_turn_request(
            '{"message":"abcdef"}',
            max_message_length=5,
        )
