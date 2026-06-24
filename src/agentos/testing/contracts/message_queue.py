from __future__ import annotations

from collections.abc import Callable

from agentos.multi import AgentEnvelope, TaskRequest, TaskResult
from agentos.multi.message_queue import AgentMessageQueue
from agentos.testing.contracts._checks import check, check_equal, check_is


def contract_envelope(
    envelope_id: str = "contract_env_request",
    *,
    to_agent_id: str = "worker",
    envelope_type: str = "task_request",
) -> AgentEnvelope:
    if envelope_type == "task_request":
        payload = TaskRequest(
            task_id=f"{envelope_id}_task",
            instruction="Run contract task.",
            required_capabilities=("message-queue-contract",),
        )
    elif envelope_type == "task_result":
        payload = TaskResult(
            task_id=f"{envelope_id}_task",
            status="completed",
            summary="Contract task complete.",
        )
    else:
        raise ValueError(f"unsupported contract envelope type: {envelope_type}")

    return AgentEnvelope(
        envelope_id=envelope_id,
        from_agent_id="parent",
        to_agent_id=to_agent_id,
        type=envelope_type,  # type: ignore[arg-type]
        payload=payload,
        created_at=1.0,
        correlation_id=f"{envelope_id}_task",
    )


def run_agent_message_queue_contract(
    factory: Callable[[], AgentMessageQueue],
    *,
    supports_duplicate_ack_false: bool = True,
    supports_wait_without_consuming: bool = True,
    supports_filtered_collect_retention: bool = True,
    supports_requeue: bool = True,
) -> None:
    _assert_send_collect_and_ack(factory, supports_duplicate_ack_false)
    _assert_inbox_isolation(factory)
    if supports_filtered_collect_retention:
        _assert_filtered_collect_retains_non_matching_deliveries(factory)
    if supports_wait_without_consuming:
        _assert_wait_reports_delivery_without_losing_it(factory)
    if supports_requeue:
        _assert_requeue_returns_unacked_delivery(factory)


def _assert_send_collect_and_ack(
    factory: Callable[[], AgentMessageQueue],
    supports_duplicate_ack_false: bool,
) -> None:
    queue = factory()
    queue.create_inbox("worker")
    queue.create_inbox("worker")
    envelope = contract_envelope()

    send_delivery_id = queue.send(envelope)
    deliveries = queue.collect("worker")

    check(
        isinstance(send_delivery_id, str),
        "send must return a delivery id string",
    )
    check(bool(send_delivery_id), "send must return a non-empty delivery id")
    check_equal(len(deliveries), 1, "collect must return one sent delivery")
    check(bool(deliveries[0].delivery_id), "delivery must include a delivery id")
    check_equal(
        deliveries[0].envelope,
        envelope,
        "collect must preserve original envelope",
    )
    check_is(
        queue.ack("worker", deliveries[0].delivery_id),
        True,
        "ack must accept an outstanding delivery",
    )
    if supports_duplicate_ack_false:
        check_is(
            queue.ack("worker", deliveries[0].delivery_id),
            False,
            "duplicate ack must return False",
        )


def _assert_inbox_isolation(factory: Callable[[], AgentMessageQueue]) -> None:
    queue = factory()
    queue.create_inbox("worker")
    queue.create_inbox("other_worker")
    envelope = contract_envelope("contract_env_isolated", to_agent_id="worker")

    queue.send(envelope)

    check_equal(
        queue.collect("other_worker"),
        [],
        "collect must isolate per-agent inboxes",
    )
    check_equal(
        [delivery.envelope for delivery in queue.collect("worker")],
        [envelope],
        "collect must return deliveries for the addressed agent",
    )


def _assert_filtered_collect_retains_non_matching_deliveries(
    factory: Callable[[], AgentMessageQueue],
) -> None:
    queue = factory()
    queue.create_inbox("worker")
    request = contract_envelope(
        "contract_env_filter_request",
        envelope_type="task_request",
    )
    result = contract_envelope(
        "contract_env_filter_result",
        envelope_type="task_result",
    )

    queue.send(request)
    queue.send(result)

    result_deliveries = queue.collect("worker", envelope_types=("task_result",))
    check_equal(
        [delivery.envelope for delivery in result_deliveries],
        [result],
        "filtered collect must return matching deliveries",
    )
    request_deliveries = queue.collect("worker")
    check_equal(
        [delivery.envelope for delivery in request_deliveries],
        [request],
        "filtered collect must retain non-matching deliveries",
    )


def _assert_wait_reports_delivery_without_losing_it(
    factory: Callable[[], AgentMessageQueue],
) -> None:
    queue = factory()
    queue.create_inbox("worker")
    envelope = contract_envelope("contract_env_wait")

    queue.send(envelope)

    check_is(
        queue.wait("worker", timeout=0.01),
        True,
        "wait must report queued delivery",
    )
    check_equal(
        [delivery.envelope for delivery in queue.collect("worker")],
        [envelope],
        "wait must not consume delivery",
    )


def _assert_requeue_returns_unacked_delivery(
    factory: Callable[[], AgentMessageQueue],
) -> None:
    queue = factory()
    queue.create_inbox("worker")
    envelope = contract_envelope("contract_env_requeue")
    queue.send(envelope)
    delivery = queue.collect("worker")[0]

    queue.requeue("worker", delivery)

    check_equal(
        queue.collect("worker"),
        [delivery],
        "requeue must return unacked delivery to the inbox",
    )


__all__ = [
    "contract_envelope",
    "run_agent_message_queue_contract",
]
