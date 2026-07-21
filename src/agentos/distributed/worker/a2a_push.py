from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from agentos.adapters.a2a import (
    A2APushAcknowledgementError,
    A2APushDeliveryError,
    A2APushResponseTooLargeError,
    A2APushSecurityError,
)
from agentos.distributed._model_validation import require_identifier
from agentos.distributed.a2a_models import (
    A2APushAttemptResolution,
    A2APushDeliveryTarget,
    A2APushFailureCategory,
    A2ATaskState,
)
from agentos.distributed.a2a_protocols import (
    A2APushDeliveryAttempt,
    A2APushDeliveryPort,
)
from agentos.distributed.errors import A2APushDeliveryDeferredError
from agentos.distributed.models import QueueDelivery
from agentos.distributed.protocols import QueuePort
from agentos.runtime.errors import PayloadProtectionError
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    PayloadProtector,
    unprotect_payload,
)
from agentos.transports.a2a.message_types import (
    A2ATaskState as WireTaskState,
    A2ATaskStatus,
    A2ATaskStatusUpdateEvent,
)
from agentos.transports.a2a.operation_types import A2AStreamResponse
from agentos.transports.a2a.serialization import encode_stream_response


_MAX_ATTEMPT_TTL = timedelta(minutes=5)
_WIRE_STATES = {
    A2ATaskState.SUBMITTED: WireTaskState.TASK_STATE_SUBMITTED,
    A2ATaskState.WORKING: WireTaskState.TASK_STATE_WORKING,
    A2ATaskState.COMPLETED: WireTaskState.TASK_STATE_COMPLETED,
    A2ATaskState.FAILED: WireTaskState.TASK_STATE_FAILED,
    A2ATaskState.CANCELED: WireTaskState.TASK_STATE_CANCELED,
    A2ATaskState.INPUT_REQUIRED: WireTaskState.TASK_STATE_INPUT_REQUIRED,
}


class A2APushClient(Protocol):
    """Push Worker 使用的 outbound HTTP Adapter 窄边界。"""

    async def deliver(
        self,
        *,
        url: str,
        stream_response: bytes,
        send_gate: A2APushDeliveryAttempt,
        authentication_scheme: str | None,
        credentials: str | None,
        token: str | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class A2APushWorker:
    """消费 Push delivery；PostgreSQL attempt 决定重试与 ACK 安全性。"""

    deliveries: A2APushDeliveryPort
    queue: QueuePort
    client: A2APushClient
    payload_protector: PayloadProtector
    worker_id: str
    topic: str
    attempt_ttl: timedelta

    def __post_init__(self) -> None:
        require_identifier(self.worker_id, "worker_id")
        require_identifier(self.topic, "topic")
        if (
            type(self.attempt_ttl) is not timedelta
            or self.attempt_ttl <= timedelta(0)
            or self.attempt_ttl > _MAX_ATTEMPT_TTL
        ):
            raise ValueError("attempt_ttl must be between 0 and 300 seconds")

    async def run_delivery(self, delivery: QueueDelivery) -> bool:
        """处理一个 at-least-once delivery；返回本次是否已 ACK。"""

        if type(delivery) is not QueueDelivery:
            raise TypeError("delivery must be QueueDelivery")
        try:
            attempt = await self.deliveries.open_attempt(
                outbox_id=delivery.outbox_id,
                worker_id=self.worker_id,
                ttl=self.attempt_ttl,
            )
        except A2APushDeliveryDeferredError:
            return False
        if attempt is None:
            await self._ack(delivery)
            return True
        target = attempt.target
        if target.outbox_id != delivery.outbox_id:
            raise RuntimeError("push attempt does not match queue delivery")

        try:
            body = _encode_target(target)
        except (TypeError, ValueError):
            return await self._record_failure(
                delivery,
                attempt,
                A2APushFailureCategory.PROTOCOL_ENCODE,
            )
        try:
            credentials, token = _open_secrets(self.payload_protector, target)
        except (PayloadProtectionError, TypeError, ValueError):
            return await self._record_failure(
                delivery,
                attempt,
                A2APushFailureCategory.SECRET_UNAVAILABLE,
            )

        try:
            await self.client.deliver(
                url=target.url,
                stream_response=body,
                send_gate=attempt,
                authentication_scheme=target.authentication_scheme,
                credentials=credentials,
                token=token,
            )
        except asyncio.CancelledError:
            raise
        except A2APushAcknowledgementError:
            category = A2APushFailureCategory.HTTP_REJECTED
        except A2APushResponseTooLargeError:
            category = A2APushFailureCategory.RESPONSE_TOO_LARGE
        except A2APushSecurityError:
            category = A2APushFailureCategory.SECURITY
        except A2APushDeliveryError:
            category = A2APushFailureCategory.NETWORK
        except ValueError:
            category = A2APushFailureCategory.SECRET_UNAVAILABLE
        else:
            await attempt.mark_delivered()
            await self._ack(delivery)
            return True
        return await self._record_failure(delivery, attempt, category)

    async def _record_failure(
        self,
        delivery: QueueDelivery,
        attempt: A2APushDeliveryAttempt,
        category: A2APushFailureCategory,
    ) -> bool:
        resolution = await attempt.mark_failed(category=category)
        if resolution is A2APushAttemptResolution.RETRY_PENDING:
            return False
        if resolution is not A2APushAttemptResolution.ACK_SAFE:
            raise RuntimeError("push attempt returned an invalid resolution")
        await self._ack(delivery)
        return True

    async def _ack(self, delivery: QueueDelivery) -> None:
        await self.queue.ack(topic=self.topic, delivery=delivery)


def _encode_target(target: A2APushDeliveryTarget) -> bytes:
    if target.protocol_version != "1.0":
        raise ValueError("unsupported push protocol version")
    try:
        state = _WIRE_STATES[target.task_state]
    except KeyError as error:
        raise ValueError("unsupported push task state") from error
    return encode_stream_response(
        A2AStreamResponse(
            status_update=A2ATaskStatusUpdateEvent(
                task_id=target.task_id,
                context_id=target.context_id,
                status=A2ATaskStatus(state),
            ),
        ),
    )


def _open_secrets(
    protector: PayloadProtector,
    target: A2APushDeliveryTarget,
) -> tuple[str | None, str | None]:
    reference = target.secret_ref
    if reference is None:
        return None, None
    payload = unprotect_payload(
        protector,
        reference,
        context=PayloadProtectionContext(
            tenant_id=target.scope.tenant_id,
            session_id=target.context_id,
        ),
    )
    if not payload or set(payload) - {"credentials", "token"}:
        raise ValueError("push secret payload is invalid")
    credentials = _secret_value(payload.get("credentials"), "credentials")
    token = _secret_value(payload.get("token"), "token")
    return credentials, token


def _secret_value(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError(f"push secret {field_name} is invalid")
    return value


__all__ = ["A2APushClient", "A2APushWorker"]
