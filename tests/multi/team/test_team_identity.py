from __future__ import annotations

from agentos.distributed.models import RequestScope
from agentos.multi.team_identity import (
    team_command_id,
    team_delivery_id,
    team_delivery_source_digest,
    team_message_id,
    team_message_request_digest,
    team_outbox_id,
    team_submission_id,
)


SCOPE = RequestScope("tenant_1", "principal_1")


def test_team_identity_fixed_vectors() -> None:
    assert team_message_id(
        scope=SCOPE,
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
    ) == (
        "team_msg_"
        "952db670afb6c29a1a0e0a695e593923d7a3478dc88aae02ee17b74fcb1cf386"
    )
    assert team_delivery_id(
        scope=SCOPE,
        team_id="team_1",
        message_id="team_msg_example",
        recipient_agent_id="agent_2",
        target_session_id="session_2",
    ) == (
        "team_delivery_"
        "a89101f55f27cab711466bb7b870a18d674f1c05cd2ea3d91e8605dfd3ef34ad"
    )
    assert team_submission_id(
        scope=SCOPE,
        delivery_id="team_delivery_example",
        target_session_id="session_2",
    ) == (
        "team_submission_"
        "e6248c85a7f65b32a245d6006a84048744d96939fcbe3d0342f39fa46f4599fe"
    )
    assert team_command_id(
        scope=SCOPE,
        delivery_id="team_delivery_example",
        target_session_id="session_2",
        run_id="run_2",
    ) == (
        "team_command_"
        "bcdd2f31352d00b041cae085be77bb9fe8729f6fb9765d6e043fa0d6b65fd287"
    )
    assert team_outbox_id(
        scope=SCOPE,
        delivery_id="team_delivery_example",
        outbox_kind="delivery_ready",
    ) == (
        "team_outbox_"
        "436015ade1ceec38f920d0f2770dcaa1b642bf19bc72b6fbd72833bf8fe680ab"
    )


def test_team_message_and_delivery_digest_fixed_vectors() -> None:
    message_digest = team_message_request_digest(
        scope=SCOPE,
        team_id="team_1",
        message_id="team_msg_example",
        sender_agent_id="agent_1",
        message_kind="instruction",
        content="检查图纸",
        correlation_id="wait_1",
        addressing_kind="direct",
        addressed_agent_id="agent_2",
    )
    delivery_digest = team_delivery_source_digest(
        scope=SCOPE,
        team_id="team_1",
        message_id="team_msg_example",
        sender_agent_id="agent_1",
        message_kind="instruction",
        content="检查图纸",
        correlation_id="wait_1",
        addressing_kind="direct",
        addressed_agent_id="agent_2",
        recipient_agent_id="agent_2",
        target_session_id="session_2",
    )

    assert message_digest == (
        "f1eb7d2b08a9ee9d6cb691caaee5e707ee13d3e3104bb202e2c1fde538c236f2"
    )
    assert delivery_digest == (
        "44f6a0d1b42616cc292cfadbba87f627fe11d242e35cedc6e426af8ec4611ece"
    )


def test_team_identity_is_key_order_and_unicode_stable() -> None:
    first = team_message_request_digest(
        scope=SCOPE,
        team_id="团队_1",
        message_id="team_msg_example",
        sender_agent_id="agent_1",
        message_kind="notice",
        content="完成",
        correlation_id=None,
        addressing_kind="broadcast",
        addressed_agent_id=None,
    )
    second = team_message_request_digest(
        addressed_agent_id=None,
        addressing_kind="broadcast",
        correlation_id=None,
        content="完成",
        message_kind="notice",
        sender_agent_id="agent_1",
        message_id="team_msg_example",
        team_id="团队_1",
        scope=SCOPE,
    )

    assert first == second
    assert len(first) == 64
