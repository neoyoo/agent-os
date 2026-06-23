import json

from agentos.multi import AgentEnvelope, TaskRequest
from agentos.multi.redis_queue import RedisAgentMessageQueue
from agentos.multi.team import TeamMessage
import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.delivered: set[tuple[str, str]] = set()
        self.acked: list[tuple[str, str, str]] = []
        self.xreadgroup_calls: list[dict[str, object]] = []

    def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = False,
    ) -> None:
        self.streams.setdefault(name, [])

    def xadd(
        self,
        name: str,
        fields: dict[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        stream = self.streams.setdefault(name, [])
        message_id = f"{len(stream) + 1}-0"
        stream.append((message_id, fields))
        return message_id

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int = 100,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        self.xreadgroup_calls.append(
            {
                "groupname": groupname,
                "consumername": consumername,
                "streams": dict(streams),
                "count": count,
                "block": block,
            },
        )
        result = []
        for name in streams:
            messages = [
                message
                for message in self.streams.get(name, [])
                if (name, message[0]) not in self.delivered
            ]
            if messages:
                selected = messages[:count]
                self.delivered.update((name, message_id) for message_id, _fields in selected)
                result.append((name, selected))
        return result

    def xread(
        self,
        streams: dict[str, str],
        count: int = 1,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        result = []
        for name in streams:
            messages = self.streams.get(name, [])
            if messages:
                result.append((name, messages[:count]))
        return result

    def xack(self, name: str, groupname: str, message_id: str) -> int:
        if any(existing_id == message_id for existing_id, _fields in self.streams.get(name, [])):
            self.acked.append((name, groupname, message_id))
            return 1
        return 0


def envelope() -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id="env_1",
        from_agent_id="parent",
        to_agent_id="worker",
        type="task_request",
        payload=TaskRequest(task_id="task_1", instruction="Do work"),
        created_at=1.0,
        correlation_id="task_1",
    )


def team_envelope() -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id="env_team_1",
        from_agent_id="leader",
        to_agent_id="worker",
        type="team_message",
        payload=TeamMessage(
            message_id="msg_1",
            team_id="team_1",
            from_agent_id="leader",
            to_agent_id="worker",
            content="Please inspect artifact.",
            kind="instruction",
            created_at=5.0,
        ),
        created_at=5.0,
        correlation_id="msg_1",
    )


def team_envelope_for(team_id: str, envelope_id: str) -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id=envelope_id,
        from_agent_id=f"leader_{team_id}",
        to_agent_id="worker",
        type="team_message",
        payload=TeamMessage(
            message_id=f"msg_{team_id}",
            team_id=team_id,
            from_agent_id=f"leader_{team_id}",
            to_agent_id="worker",
            content=f"Please inspect {team_id}.",
            kind="instruction",
            created_at=5.0,
        ),
        created_at=5.0,
        correlation_id=f"msg_{team_id}",
    )


def test_redis_queue_requires_explicit_consumer_scope_by_default() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client)
    queue.create_inbox("worker")
    delivery_id = queue.send(envelope())

    with pytest.raises(PermissionError, match="consumer scope"):
        queue.collect("worker")
    with pytest.raises(PermissionError, match="consumer scope"):
        queue.wait("worker", timeout=0)
    with pytest.raises(PermissionError, match="consumer scope"):
        queue.ack("worker", delivery_id)


def test_redis_queue_can_explicitly_allow_unscoped_local_consumers() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(
        url="redis://unused",
        client=client,
        allow_unscoped_consumers=True,
    )
    queue.create_inbox("worker")
    queue.send(envelope())

    assert queue.collect("worker")[0].envelope == envelope()


def test_redis_queue_sends_collects_and_acks_envelope() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(
        url="redis://unused",
        client=client,
        allowed_consumer_agent_ids=("worker",),
    )
    queue.create_inbox("worker")

    delivery_id = queue.send(envelope())
    deliveries = queue.collect("worker")

    assert deliveries[0].delivery_id.startswith("redis-stream:")
    assert deliveries[0].envelope == envelope()
    assert queue.ack("worker", deliveries[0].delivery_id) is True
    assert client.acked == [
        ("agentos:multi:inbox:worker", "agentos-workers", delivery_id),
    ]


def test_redis_queue_collects_payload_from_redis_py_byte_fields() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(
        url="redis://unused",
        client=client,
        allowed_consumer_agent_ids=("worker",),
    )
    queue.create_inbox("worker")
    stream_key = "agentos:multi:inbox:worker"
    payload = json.dumps(
        {
            "envelope_id": "env_1",
            "from_agent_id": "parent",
            "to_agent_id": "worker",
            "type": "task_request",
            "payload": {"task_id": "task_1", "instruction": "Do work"},
            "created_at": 1.0,
            "correlation_id": "task_1",
        },
    )
    client.streams[stream_key].append(
        (
            b"1-0",
            {b"payload": payload.encode("utf-8")},
        ),
    )

    deliveries = queue.collect("worker")

    assert deliveries[0].delivery_id.startswith("redis-stream:")
    assert deliveries[0].envelope == envelope()


def test_redis_queue_wait_does_not_consume_delivery() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")

    queue.send(envelope())

    assert queue.wait("worker", timeout=0.01) is True
    assert queue.collect("worker")[0].envelope == envelope()


def test_redis_queue_round_trips_team_message_envelope() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")

    queue.send(team_envelope())

    assert queue.collect("worker")[0].envelope == team_envelope()


def test_redis_queue_collects_team_messages_from_team_scoped_streams() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")
    team_2 = team_envelope_for("team_2", "env_team_2")
    team_1 = team_envelope_for("team_1", "env_team_1")
    queue.send(team_2)
    queue.send(team_1)

    team_1_deliveries = queue.collect_team_messages("worker", team_id="team_1")
    team_2_deliveries = queue.collect_team_messages("worker", team_id="team_2")

    assert [delivery.envelope for delivery in team_1_deliveries] == [team_1]
    assert [delivery.envelope for delivery in team_2_deliveries] == [team_2]


def test_redis_queue_filtered_collect_reads_only_matching_type_stream() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")
    queue.send(envelope())
    queue.send(team_envelope())

    team_deliveries = queue.collect("worker", envelope_types=("team_message",))

    assert [delivery.envelope for delivery in team_deliveries] == [team_envelope()]
    assert [delivery.envelope for delivery in queue.collect("worker")] == [envelope()]


def test_redis_queue_ack_uses_delivery_stream_identity() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")
    queue.send(envelope())
    queue.send(team_envelope())

    deliveries = queue.collect("worker")
    team_delivery = [
        delivery
        for delivery in deliveries
        if delivery.envelope.type == "team_message"
    ][0]

    assert team_delivery.delivery_id.startswith("redis-stream:")
    assert queue.ack("worker", team_delivery.delivery_id) is True
    assert client.acked == [
        (
            "agentos:multi:inbox:worker:team_message:team_1",
            "agentos-workers",
            "1-0",
        ),
    ]


def test_redis_queue_does_not_ack_delivery_for_wrong_agent_stream() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(
        url="redis://unused",
        client=client,
        allowed_consumer_agent_ids=("worker", "other_worker"),
    )
    queue.create_inbox("worker")
    queue.create_inbox("other_worker")
    queue.send(envelope())
    delivery = queue.collect("worker")[0]

    assert queue.ack("other_worker", delivery.delivery_id) is False
    assert client.acked == []
    assert queue.ack("worker", delivery.delivery_id) is True
    assert client.acked == [
        ("agentos:multi:inbox:worker", "agentos-workers", "1-0"),
    ]


def test_redis_queue_scoped_consumer_rejects_wrong_agent_id() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(
        url="redis://unused",
        client=client,
        allowed_consumer_agent_ids=("worker",),
    )
    queue.create_inbox("worker")
    queue.create_inbox("other_worker")
    queue.send(envelope())
    delivery = queue.collect("worker")[0]

    with pytest.raises(PermissionError, match="other_worker"):
        queue.collect("other_worker")
    with pytest.raises(PermissionError, match="other_worker"):
        queue.ack("other_worker", delivery.delivery_id)


def test_redis_queue_scoped_consumer_rejects_wait_and_team_collect() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(
        url="redis://unused",
        client=client,
        allowed_consumer_agent_ids=("worker",),
    )
    queue.create_inbox("worker")
    queue.send(team_envelope())

    with pytest.raises(PermissionError, match="other_worker"):
        queue.wait("other_worker", timeout=0)
    with pytest.raises(PermissionError, match="other_worker"):
        queue.collect_team_messages("other_worker", team_id="team_1")


def test_redis_queue_legacy_raw_ack_remains_compatible() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")

    delivery_id = queue.send(envelope())

    assert queue.ack("worker", delivery_id) is True
    assert client.acked == [
        ("agentos:multi:inbox:worker", "agentos-workers", delivery_id),
    ]


def test_redis_queue_wait_timeout_zero_is_nonblocking() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")

    assert queue.wait("worker", timeout=0) is False

    wait_calls = client.xreadgroup_calls[-1:]
    assert len(wait_calls) == 1
    assert wait_calls[0]["block"] is None


def test_redis_queue_wait_none_uses_blocking_read() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")

    assert queue.wait("worker", timeout=None) is False

    wait_calls = client.xreadgroup_calls[-1:]
    assert len(wait_calls) == 1
    assert wait_calls[0]["block"] == 0


def test_redis_queue_wait_reads_all_type_streams_together() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client, allowed_consumer_agent_ids=("worker",))
    queue.create_inbox("worker")
    queue.send(team_envelope())

    assert queue.wait("worker", timeout=0.01) is True

    wait_call = client.xreadgroup_calls[-1]
    assert set(wait_call["streams"]) == {
        "agentos:multi:inbox:worker",
        "agentos:multi:inbox:worker:task_result",
        "agentos:multi:inbox:worker:team_message",
        "agentos:multi:inbox:worker:team_message:team_1",
    }
    assert queue.collect("worker")[0].envelope == team_envelope()
