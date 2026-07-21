from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math

from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import ChannelAuthenticator, ChannelServices
from agentos.distributed.models import ReplayItem, StreamGap
from agentos.distributed.protocols import EventSubscription
from agentos.transports.http.request_decoder import decode_last_event_id
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.http.response_encoder import encode_error_response
from agentos.transports.http.response_types import HttpResponse
from agentos.transports.sse.codec import (
    encode_gap,
    encode_heartbeat,
    encode_replay_event,
    is_terminal_event,
)
from agentos.transports.run_stream import decode_cursor


class RunSseResponse:
    """Closeable SSE stream with heartbeat and exactly-once subscription release."""

    __slots__ = (
        "_closed",
        "_heartbeat_interval",
        "_next_task",
        "_subscription",
    )

    status_code = 200
    headers = (
        ("Content-Type", "text/event-stream; charset=utf-8"),
        ("Cache-Control", "no-cache"),
        ("X-Accel-Buffering", "no"),
    )

    def __init__(
        self,
        subscription: EventSubscription,
        *,
        heartbeat_interval: float,
    ) -> None:
        if (
            type(heartbeat_interval) not in (int, float)
            or not math.isfinite(heartbeat_interval)
            or heartbeat_interval <= 0
        ):
            raise ValueError("heartbeat_interval must be positive")
        self._subscription = subscription
        self._heartbeat_interval = float(heartbeat_interval)
        self._next_task: asyncio.Task[ReplayItem | StreamGap] | None = None
        self._closed = False

    async def next_frame(self) -> bytes:
        if self._closed:
            raise StopAsyncIteration
        if self._next_task is None:
            self._next_task = asyncio.create_task(anext(self._subscription))
        task = self._next_task
        try:
            done, _ = await asyncio.wait(
                (task,),
                timeout=self._heartbeat_interval,
            )
        except asyncio.CancelledError:
            await self.aclose()
            raise
        if not done:
            return encode_heartbeat().encode("utf-8")
        self._next_task = None
        try:
            item = task.result()
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException:
            await self.aclose()
            raise
        try:
            if type(item) is StreamGap:
                frame = encode_gap(item)
                terminal = True
            elif type(item) is ReplayItem:
                frame = encode_replay_event(item)
                terminal = is_terminal_event(item.event.event)
            else:
                raise TypeError("event subscription returned an invalid item")
        except Exception:
            await self.aclose()
            raise
        if terminal:
            await self.aclose()
        return frame.encode("utf-8")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._next_task
        self._next_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            finally:
                await self._subscription.aclose()
            return
        await self._subscription.aclose()


@dataclass(frozen=True, slots=True)
class RunSseEndpoint:
    """Authenticates and opens a scoped Run replay/tail stream."""

    services: ChannelServices
    authenticator: ChannelAuthenticator
    heartbeat_interval: float = 15.0

    async def open(
        self,
        *,
        session_id: str,
        run_id: str,
        headers: HttpHeaders,
        request_id: str,
    ) -> RunSseResponse | HttpResponse:
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=ChannelAuthContext(
                    operation="subscribe_run",
                    method="GET",
                    path=f"/v1/sessions/{session_id}/runs/{run_id}/events",
                    session_id=session_id,
                    resource_type="run",
                    resource_id=run_id,
                ),
            )
            public_cursor = decode_last_event_id(headers)
            cursor = (
                None
                if public_cursor is None
                else decode_cursor(
                    public_cursor,
                    tenant_id=scope.tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                )
            )
            subscription = await self.services.run_events.subscribe(
                scope,
                session_id,
                run_id,
                cursor,
            )
            return RunSseResponse(
                subscription,
                heartbeat_interval=self.heartbeat_interval,
            )
        except Exception as error:
            return encode_error_response(error, request_id=request_id)


__all__ = ["RunSseEndpoint", "RunSseResponse"]
