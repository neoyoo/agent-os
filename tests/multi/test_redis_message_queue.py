from agentos.multi import AgentEnvelope, TaskRequest
from agentos.multi.redis_queue import RedisAgentMessageQueue


class FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.delivered: set[str] = set()
        self.acked: list[tuple[str, str, str]] = []

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
        result = []
        for name in streams:
            messages = [
                message
                for message in self.streams.get(name, [])
                if message[0] not in self.delivered
            ]
            if messages:
                selected = messages[:count]
                self.delivered.update(message_id for message_id, _fields in selected)
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
        self.acked.append((name, groupname, message_id))
        return 1


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


def test_redis_queue_sends_collects_and_acks_envelope() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client)
    queue.create_inbox("worker")

    delivery_id = queue.send(envelope())
    deliveries = queue.collect("worker")

    assert deliveries[0].delivery_id == delivery_id
    assert deliveries[0].envelope == envelope()
    assert queue.ack("worker", delivery_id) is True
    assert client.acked == [
        ("agentos:multi:inbox:worker", "agentos-workers", delivery_id),
    ]


def test_redis_queue_wait_does_not_consume_delivery() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client)
    queue.create_inbox("worker")

    queue.send(envelope())

    assert queue.wait("worker", timeout=0.01) is True
    assert queue.collect("worker")[0].envelope == envelope()
