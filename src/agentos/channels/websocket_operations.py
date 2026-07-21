from __future__ import annotations

from dataclasses import dataclass

from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import ChannelAuthenticator, ChannelServices
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.protocols import EventSubscription
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.transports.http.errors import PermissionDeniedError
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.run_stream import decode_cursor
from agentos.transports.websocket import (
    ReceiptOperation,
    ReceiptFrame,
    SubmitCommandFrame,
    SubmitCommandReceiptData,
    SubmitRunFrame,
    SubmitRunReceiptData,
    SubscribeRunFrame,
    SubscriptionReceiptData,
    UnsubscribeRunFrame,
)


@dataclass(frozen=True, slots=True)
class WebSocketRunOperations:
    """Resource authorization and Run Application Service adapter."""

    services: ChannelServices
    authenticator: ChannelAuthenticator
    handshake_scope: RequestScope
    headers: HttpHeaders

    def __post_init__(self) -> None:
        if type(self.services) is not ChannelServices:
            raise TypeError("services must be ChannelServices")
        if type(self.handshake_scope) is not RequestScope:
            raise TypeError("handshake_scope must be RequestScope")
        if type(self.headers) is not HttpHeaders:
            raise TypeError("headers must be HttpHeaders")

    async def submit_run(self, frame: SubmitRunFrame) -> ReceiptFrame:
        scope = await self.authorize("submit_run", frame.session_id, None)
        receipt = await self.services.run_submissions.submit(
            scope,
            RunSubmission(
                frame.session_id,
                frame.request_id,
                frame.content,
                frame.artifact_handles,
            ),
        )
        return ReceiptFrame(
            frame.request_id,
            "submit_run",
            SubmitRunReceiptData(
                receipt.session_id,
                receipt.run_id,
                receipt.submission_id,
                receipt.aggregate_version,
                receipt.duplicate,
            ),
        )

    async def submit_command(self, frame: SubmitCommandFrame) -> ReceiptFrame:
        scope = await self.authorize(
            "submit_command",
            frame.session_id,
            frame.run_id,
        )
        receipt = await self.services.run_commands.submit(
            scope,
            frame.session_id,
            DurableRunCommand(
                frame.run_id,
                frame.request_id,
                frame.kind,
                frame.payload,
            ),
        )
        return ReceiptFrame(
            frame.request_id,
            "submit_command",
            SubmitCommandReceiptData(
                receipt.run_id,
                receipt.command_id,
                receipt.kind,
                receipt.aggregate_version,
                receipt.duplicate,
            ),
        )

    async def subscribe(
        self,
        frame: SubscribeRunFrame,
        *,
        scope: RequestScope,
    ) -> EventSubscription:
        cursor = (
            None
            if frame.cursor is None
            else decode_cursor(
                frame.cursor,
                tenant_id=scope.tenant_id,
                session_id=frame.session_id,
                run_id=frame.run_id,
            )
        )
        return await self.services.run_events.subscribe(
            scope,
            frame.session_id,
            frame.run_id,
            cursor,
        )

    async def authorize_subscription(
        self,
        frame: SubscribeRunFrame | UnsubscribeRunFrame,
        operation: str,
    ) -> RequestScope:
        return await self.authorize(operation, frame.session_id, frame.run_id)

    def subscription_receipt(
        self,
        frame: SubscribeRunFrame | UnsubscribeRunFrame,
        operation: ReceiptOperation,
    ) -> ReceiptFrame:
        if operation not in {"subscribe_run", "unsubscribe_run"}:
            raise ValueError("subscription receipt operation is invalid")
        return ReceiptFrame(
            frame.request_id,
            operation,
            SubscriptionReceiptData(frame.session_id, frame.run_id),
        )

    async def authorize(
        self,
        operation: str,
        session_id: str,
        run_id: str | None,
    ) -> RequestScope:
        scope = await self.authenticator.authenticate(
            self.headers,
            context=ChannelAuthContext(
                operation=operation,
                method="WEBSOCKET",
                path="/v1/ws",
                session_id=session_id,
                resource_type="run" if run_id is not None else "session",
                resource_id=run_id if run_id is not None else session_id,
            ),
        )
        if scope != self.handshake_scope:
            raise PermissionDeniedError()
        return scope


__all__ = ["WebSocketRunOperations"]
