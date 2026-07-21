from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json

import pytest

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.adapters.a2a import (
    A2APushAcknowledgementError,
    A2APushDeliveryError,
    A2APushResponseTooLargeError,
    A2APushSecurityError,
)
from agentos.distributed.a2a_models import (
    A2APushAttemptResolution,
    A2APushDeliveryTarget,
    A2APushFailureCategory,
    A2ATaskState,
)
from agentos.distributed.errors import A2APushDeliveryDeferredError
from agentos.distributed.models import QueueDelivery, RequestScope
from agentos.distributed.worker.a2a_push import A2APushWorker
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    ProtectedPayloadRef,
)
from tests.planning._async import async_test


NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)
SCOPE = RequestScope("tenant_1", "principal_1")
DELIVERY = QueueDelivery("1-0", "outbox_1", 1)


def _target(
    *,
    secret_ref: ProtectedPayloadRef | None = ProtectedPayloadRef(
        "sealed-secret",
        "secret-digest",
    ),
    protocol_version: str = "1.0",
) -> A2APushDeliveryTarget:
    return A2APushDeliveryTarget(
        scope=SCOPE,
        outbox_id=DELIVERY.outbox_id,
        delivery_id="delivery_1",
        task_id="run_1",
        context_id="session_1",
        config_id="push_1",
        url="https://push.example.test/hook",
        authentication_scheme="Bearer",
        secret_ref=secret_ref,
        event_id="event_1",
        protocol_version=protocol_version,
        status_sequence=7,
        task_state=A2ATaskState.COMPLETED,
        delivered_at=None,
        suppressed_at=None,
        abandoned_at=None,
    )


class FakeAttempt:
    def __init__(
        self,
        trace: list[str],
        *,
        target: A2APushDeliveryTarget | None = None,
        resolution: A2APushAttemptResolution = A2APushAttemptResolution.RETRY_PENDING,
    ) -> None:
        self.target = _target() if target is None else target
        self.attempt_id = "attempt_1"
        self.expires_at = NOW + timedelta(minutes=1)
        self.trace = trace
        self.resolution = resolution
        self.failed: list[A2APushFailureCategory] = []
        self.mark_delivered_error: BaseException | None = None
        self.mark_failed_error: BaseException | None = None

    @asynccontextmanager
    async def authorize_send(self) -> AsyncIterator[None]:
        self.trace.append("attempt.authorize")
        yield

    async def mark_delivered(self) -> None:
        self.trace.append("attempt.delivered")
        if self.mark_delivered_error is not None:
            raise self.mark_delivered_error

    async def mark_failed(
        self,
        *,
        category: A2APushFailureCategory,
    ) -> A2APushAttemptResolution:
        self.trace.append(f"attempt.failed:{category.value}")
        self.failed.append(category)
        if self.mark_failed_error is not None:
            raise self.mark_failed_error
        return self.resolution


class FakeDeliveryPort:
    def __init__(
        self,
        trace: list[str],
        attempt: FakeAttempt | None,
    ) -> None:
        self.trace = trace
        self.attempt = attempt
        self.error: BaseException | None = None
        self.calls: list[tuple[str, str, timedelta]] = []

    async def open_attempt(
        self,
        *,
        outbox_id: str,
        worker_id: str,
        ttl: timedelta,
    ) -> FakeAttempt | None:
        self.trace.append("postgres.open_attempt")
        self.calls.append((outbox_id, worker_id, ttl))
        if self.error is not None:
            raise self.error
        return self.attempt


class FakeQueue:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.acked: list[QueueDelivery] = []

    async def ack(self, *, topic: str, delivery: QueueDelivery) -> None:
        self.trace.append("queue.ack")
        self.acked.append(delivery)


class FakeClient:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.error: BaseException | None = None
        self.calls: list[dict[str, object]] = []

    async def deliver(self, **kwargs: object) -> None:
        self.trace.append("client.deliver")
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class FakeProtector:
    def __init__(self) -> None:
        self.payload: FrozenJsonObject = freeze_json_mapping(
            {"credentials": "credential-secret", "token": "notification-secret"}
        )
        self.error: BaseException | None = None
        self.calls: list[tuple[ProtectedPayloadRef, PayloadProtectionContext]] = []

    def protect(self, *_: object, **__: object) -> ProtectedPayloadRef:
        raise AssertionError("push worker must not protect payloads")

    def unprotect(
        self,
        reference: ProtectedPayloadRef,
        *,
        context: PayloadProtectionContext,
    ) -> FrozenJsonObject:
        self.calls.append((reference, context))
        if self.error is not None:
            raise self.error
        return self.payload


def _worker(
    trace: list[str],
    *,
    attempt: FakeAttempt | None = None,
) -> tuple[A2APushWorker, FakeDeliveryPort, FakeQueue, FakeClient, FakeProtector]:
    selected = FakeAttempt(trace) if attempt is None else attempt
    port = FakeDeliveryPort(trace, selected)
    queue = FakeQueue(trace)
    client = FakeClient(trace)
    protector = FakeProtector()
    return (
        A2APushWorker(
            deliveries=port,  # type: ignore[arg-type]
            queue=queue,  # type: ignore[arg-type]
            client=client,  # type: ignore[arg-type]
            payload_protector=protector,
            worker_id="push_worker_1",
            topic="agentos.a2a.push",
            attempt_ttl=timedelta(minutes=1),
        ),
        port,
        queue,
        client,
        protector,
    )


@async_test
async def test_push_worker_sends_direct_stream_response_then_marks_and_acks() -> None:
    trace: list[str] = []
    worker, port, queue, client, protector = _worker(trace)

    assert await worker.run_delivery(DELIVERY) is True

    assert port.calls == [(DELIVERY.outbox_id, "push_worker_1", timedelta(minutes=1))]
    assert protector.calls == [
        (
            ProtectedPayloadRef("sealed-secret", "secret-digest"),
            PayloadProtectionContext("tenant_1", "session_1"),
        )
    ]
    assert trace == [
        "postgres.open_attempt",
        "client.deliver",
        "attempt.delivered",
        "queue.ack",
    ]
    assert queue.acked == [DELIVERY]
    call = client.calls[0]
    assert call["url"] == "https://push.example.test/hook"
    assert call["send_gate"] is port.attempt
    assert call["authentication_scheme"] == "Bearer"
    assert call["credentials"] == "credential-secret"
    assert call["token"] == "notification-secret"
    assert json.loads(call["stream_response"]) == {
        "statusUpdate": {
            "contextId": "session_1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "taskId": "run_1",
        }
    }


@async_test
async def test_push_worker_acks_terminal_delivery_without_decrypting_or_sending() -> None:
    trace: list[str] = []
    worker, port, queue, client, protector = _worker(trace)
    port.attempt = None

    assert await worker.run_delivery(DELIVERY) is True

    assert trace == ["postgres.open_attempt", "queue.ack"]
    assert queue.acked == [DELIVERY]
    assert client.calls == []
    assert protector.calls == []


@async_test
async def test_push_worker_leaves_deferred_delivery_pending() -> None:
    trace: list[str] = []
    worker, port, queue, client, _ = _worker(trace)
    port.error = A2APushDeliveryDeferredError()

    assert await worker.run_delivery(DELIVERY) is False

    assert queue.acked == []
    assert client.calls == []


@pytest.mark.parametrize(
    ("error", "category"),
    (
        (A2APushAcknowledgementError(), A2APushFailureCategory.HTTP_REJECTED),
        (A2APushDeliveryError(), A2APushFailureCategory.NETWORK),
        (A2APushSecurityError(), A2APushFailureCategory.SECURITY),
        (
            A2APushResponseTooLargeError(),
            A2APushFailureCategory.RESPONSE_TOO_LARGE,
        ),
    ),
)
@async_test
async def test_push_worker_records_typed_delivery_failure_without_ack(
    error: BaseException,
    category: A2APushFailureCategory,
) -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace)
    worker, _, queue, client, _ = _worker(trace, attempt=attempt)
    client.error = error

    assert await worker.run_delivery(DELIVERY) is False

    assert attempt.failed == [category]
    assert queue.acked == []


@async_test
async def test_push_worker_acks_only_when_failed_attempt_is_terminal() -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace, resolution=A2APushAttemptResolution.ACK_SAFE)
    worker, _, queue, client, _ = _worker(trace, attempt=attempt)
    client.error = A2APushAcknowledgementError()

    assert await worker.run_delivery(DELIVERY) is True

    assert trace[-2:] == ["attempt.failed:http_rejected", "queue.ack"]
    assert queue.acked == [DELIVERY]


@pytest.mark.parametrize("invalid_secret", ("unprotect", "shape"))
@async_test
async def test_push_worker_records_secret_failure_before_network(
    invalid_secret: str,
) -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace)
    worker, _, queue, client, protector = _worker(trace, attempt=attempt)
    if invalid_secret == "unprotect":
        protector.error = RuntimeError("kms detail must be redacted")
    else:
        protector.payload = freeze_json_mapping({"credentials": 42})

    assert await worker.run_delivery(DELIVERY) is False

    assert attempt.failed == [A2APushFailureCategory.SECRET_UNAVAILABLE]
    assert client.calls == []
    assert queue.acked == []


@async_test
async def test_push_worker_records_protocol_failure_before_network() -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace, target=_target(protocol_version="2.0"))
    worker, _, queue, client, _ = _worker(trace, attempt=attempt)

    assert await worker.run_delivery(DELIVERY) is False

    assert attempt.failed == [A2APushFailureCategory.PROTOCOL_ENCODE]
    assert client.calls == []
    assert queue.acked == []


@async_test
async def test_push_worker_does_not_ack_when_delivery_commit_fails() -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace)
    attempt.mark_delivered_error = RuntimeError("postgres unavailable")
    worker, _, queue, _, _ = _worker(trace, attempt=attempt)

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await worker.run_delivery(DELIVERY)

    assert queue.acked == []


@async_test
async def test_push_worker_does_not_ack_when_failure_commit_fails() -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace)
    attempt.mark_failed_error = RuntimeError("postgres unavailable")
    worker, _, queue, client, _ = _worker(trace, attempt=attempt)
    client.error = A2APushDeliveryError()

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await worker.run_delivery(DELIVERY)

    assert queue.acked == []


@async_test
async def test_push_worker_cancellation_does_not_record_failure_or_ack() -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace)
    worker, _, queue, client, _ = _worker(trace, attempt=attempt)
    client.error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await worker.run_delivery(DELIVERY)

    assert attempt.failed == []
    assert queue.acked == []


def test_push_worker_configuration_is_bounded() -> None:
    trace: list[str] = []
    attempt = FakeAttempt(trace)
    with pytest.raises(ValueError, match="attempt_ttl"):
        A2APushWorker(
            deliveries=FakeDeliveryPort(trace, attempt),  # type: ignore[arg-type]
            queue=FakeQueue(trace),  # type: ignore[arg-type]
            client=FakeClient(trace),  # type: ignore[arg-type]
            payload_protector=FakeProtector(),
            worker_id="push_worker_1",
            topic="agentos.a2a.push",
            attempt_ttl=timedelta(seconds=301),
        )
