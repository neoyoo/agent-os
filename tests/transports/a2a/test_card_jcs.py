from __future__ import annotations

import struct

import pytest

from agentos.transports.a2a.card_types import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentCardSignature,
    A2AAgentInterface,
    A2AAgentSkill,
)
from agentos.transports.a2a.serialization import agent_card_signing_payload
from agentos.transports.a2a._jcs import jcs_bytes


def test_card_signing_payload_uses_jcs_and_excludes_signatures() -> None:
    card = A2AAgentCard(
        name="reviewer",
        description="reviews code",
        supported_interfaces=(
            A2AAgentInterface(
                url="https://agent.example/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        version="1.0.0",
        capabilities=A2AAgentCapabilities(streaming=False),
        default_input_modes=("text/plain",),
        default_output_modes=("text/plain",),
        skills=(
            A2AAgentSkill(
                id="review",
                name="Review",
                description="Review code",
                tags=("code",),
            ),
        ),
        signatures=(A2AAgentCardSignature("cHJvdGVjdGVk", "c2ln"),),
    )

    payload = agent_card_signing_payload(card)

    assert b"signatures" not in payload
    assert b'"streaming":false' in payload
    assert payload == (
        b'{"capabilities":{"streaming":false},"defaultInputModes":["text/plain"],'
        b'"defaultOutputModes":["text/plain"],"description":"reviews code",'
        b'"name":"reviewer","skills":[{"description":"Review code","id":"review",'
        b'"name":"Review","tags":["code"]}],"supportedInterfaces":'
        b'[{"protocolBinding":"JSONRPC","protocolVersion":"1.0",'
        b'"url":"https://agent.example/a2a"}],"version":"1.0.0"}'
    )


@pytest.mark.parametrize(
    ("ieee_754", "expected"),
    [
        ("0000000000000000", "0"),
        ("8000000000000000", "0"),
        ("0000000000000001", "5e-324"),
        ("8000000000000001", "-5e-324"),
        ("7fefffffffffffff", "1.7976931348623157e+308"),
        ("ffefffffffffffff", "-1.7976931348623157e+308"),
        ("4340000000000000", "9007199254740992"),
        ("c340000000000000", "-9007199254740992"),
        ("4430000000000000", "295147905179352830000"),
        ("44b52d02c7e14af5", "9.999999999999997e+22"),
        ("44b52d02c7e14af6", "1e+23"),
        ("44b52d02c7e14af7", "1.0000000000000001e+23"),
        ("444b1ae4d6e2ef4e", "999999999999999700000"),
        ("444b1ae4d6e2ef4f", "999999999999999900000"),
        ("444b1ae4d6e2ef50", "1e+21"),
        ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
        ("3eb0c6f7a0b5ed8d", "0.000001"),
        ("41b3de4355555553", "333333333.3333332"),
        ("41b3de4355555554", "333333333.33333325"),
        ("41b3de4355555555", "333333333.3333333"),
        ("41b3de4355555556", "333333333.3333334"),
        ("41b3de4355555557", "333333333.33333343"),
        ("becbf647612f3696", "-0.0000033333333333333333"),
        ("43143ff3c1cb0959", "1424953923781206.2"),
    ],
)
def test_jcs_matches_rfc_8785_appendix_b_number_vectors(
    ieee_754: str,
    expected: str,
) -> None:
    value = struct.unpack(">d", bytes.fromhex(ieee_754))[0]

    assert jcs_bytes({"n": value}) == f'{{"n":{expected}}}'.encode()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_jcs_rejects_nonfinite_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        jcs_bytes({"n": value})
