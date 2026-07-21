from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentos.transports.websocket import (
    SubmitCommandFrame,
    SubmitRunFrame,
    SubscribeRunFrame,
    UnsubscribeRunFrame,
    WebSocketDecodeError,
    WebSocketFrameTooLargeError,
    decode_client_frame,
)


ARTIFACT_ID = "art_12345678-1234-4123-8123-123456789abc"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '{"type":"submit_run","request_id":"req_1","session_id":"s1",'
            f'"content":"analyse","artifact_handles":["{ARTIFACT_ID}"]}}',
            SubmitRunFrame("req_1", "s1", "analyse", (ARTIFACT_ID,)),
        ),
        (
            '{"type":"submit_command","request_id":"req_2",'
            '"session_id":"s1","run_id":"r1","kind":"cancel",'
            '"payload":{}}',
            SubmitCommandFrame("req_2", "s1", "r1", "cancel", {}),
        ),
        (
            '{"type":"subscribe_run","request_id":"req_3",'
            '"session_id":"s1","run_id":"r1","cursor":null}',
            SubscribeRunFrame("req_3", "s1", "r1", None),
        ),
        (
            '{"type":"unsubscribe_run","request_id":"req_4",'
            '"session_id":"s1","run_id":"r1"}',
            UnsubscribeRunFrame("req_4", "s1", "r1"),
        ),
    ],
)
def test_decode_client_frame_returns_frozen_typed_dto(
    text: str,
    expected: object,
) -> None:
    frame = decode_client_frame(text)

    assert frame == expected
    with pytest.raises(FrozenInstanceError):
        frame.request_id = "other"  # type: ignore[misc, union-attr]


def test_submit_command_uses_canonical_kind_and_payload_contract() -> None:
    frame = decode_client_frame(
        '{"type":"submit_command","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","kind":"hitl_answer",'
        '"payload":{"approved":true}}',
    )

    assert frame == SubmitCommandFrame(
        "req_1",
        "s1",
        "r1",
        "hitl_answer",
        {"approved": True},
    )
    with pytest.raises(WebSocketDecodeError) as invalid_kind:
        decode_client_frame(
            '{"type":"submit_command","request_id":"req_2",'
            '"session_id":"s1","run_id":"r1","kind":"unknown",'
            '"payload":{}}',
        )
    assert invalid_kind.value.request_id == "req_2"


@pytest.mark.parametrize(
    "text",
    [
        '{"type":"subscribe_run","request_id":"req_1",'
        '"request_id":"req_2","session_id":"s1","run_id":"r1",'
        '"cursor":null}',
        '{"type":"submit_command","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","kind":"cancel",'
        '"payload":{"value":1,"value":2}}',
        '{"type":"subscribe_run","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","cursor":null,"extra":true}',
        '{"type":"unknown","request_id":"req_1"}',
        '{"type":"submit_command","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","kind":"cancel",'
        '"payload":{"value":NaN}}',
        '{"type":"submit_command","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","kind":"cancel",'
        '"payload":{"value":Infinity}}',
        '{"type":"submit_command","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","kind":"cancel",'
        '"payload":{"value":1e400}}',
        '{"type":"subscribe_run","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","cursor":true}',
        '{"type":"submit_run","request_id":"req_1",'
        '"session_id":"s1","content":"bad\\u0000value",'
        '"artifact_handles":[]}',
        '{"type":"submit_command","request_id":"req_1",'
        '"session_id":"s1","run_id":"r1","kind":"cancel",'
        '"payload":{"bad\\u0000key":true}}',
    ],
)
def test_decode_rejects_noncanonical_or_unsafe_objects(text: str) -> None:
    with pytest.raises(WebSocketDecodeError, match="^invalid request$"):
        decode_client_frame(text)


def test_decode_error_only_preserves_a_trustworthy_request_id() -> None:
    with pytest.raises(WebSocketDecodeError) as known:
        decode_client_frame(
            '{"type":"subscribe_run","request_id":"req_1",'
            '"session_id":"s1","run_id":"r1","cursor":false}',
        )
    with pytest.raises(WebSocketDecodeError) as duplicate:
        decode_client_frame(
            '{"type":"subscribe_run","request_id":"req_1",'
            '"request_id":"req_2","session_id":"s1","run_id":"r1",'
            '"cursor":null}',
        )
    with pytest.raises(WebSocketDecodeError) as malformed:
        decode_client_frame("{")

    assert known.value.request_id == "req_1"
    assert duplicate.value.request_id is None
    assert malformed.value.request_id is None


def test_decode_rejects_non_text_and_oversized_text_separately() -> None:
    with pytest.raises(TypeError, match="text must be str"):
        decode_client_frame(b"{}")  # type: ignore[arg-type]
    with pytest.raises(WebSocketFrameTooLargeError, match="frame is too large"):
        decode_client_frame(" " * (256 * 1024 + 1))
