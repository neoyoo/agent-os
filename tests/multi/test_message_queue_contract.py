from agentos.multi import AgentEnvelope, AgentInbox, TaskRequest


def request_envelope(envelope_id: str = "env_1") -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id=envelope_id,
        from_agent_id="parent",
        to_agent_id="worker",
        type="task_request",
        payload=TaskRequest(task_id="task_1", instruction="Do work"),
        created_at=1.0,
        correlation_id="task_1",
    )


def test_agent_inbox_returns_delivery_ids_and_acks() -> None:
    queue = AgentInbox()
    queue.create_inbox("worker")

    delivery_id = queue.send(request_envelope())
    deliveries = queue.collect("worker")

    assert deliveries[0].delivery_id == delivery_id
    assert deliveries[0].envelope == request_envelope()
    assert queue.ack("worker", delivery_id) is True
    assert queue.ack("worker", delivery_id) is False
