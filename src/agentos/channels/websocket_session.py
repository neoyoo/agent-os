from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Protocol, TypeVar

from agentos.channels.asgi_websocket import (
    WebSocketBinaryFrame,
    WebSocketDisconnected,
    WebSocketFrameTooLarge,
    WebSocketProtocolViolation,
)
from agentos.channels.service_wiring import ChannelAuthenticator, ChannelServices
from agentos.channels.websocket_buffer import (
    WebSocketBufferClosed,
    WebSocketBufferLimits,
    WebSocketBufferOverflow,
    WebSocketOutboundBuffer,
    WebSocketSubscriptionKey,
)
from agentos.channels.websocket_operations import WebSocketRunOperations
from agentos.channels.websocket_subscription import (
    WebSocketSubscriptionPump,
    WebSocketSubscriptionState,
)
from agentos.distributed.models import RequestScope
from agentos.transports.http import map_http_error
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.run_stream import RunStreamCursorError
from agentos.transports.websocket import (
    ErrorFrame,
    ResumeCursor,
    ServerFrame,
    SubmitCommandFrame,
    SubmitRunFrame,
    SubscribeRunFrame,
    UnsubscribeRunFrame,
    WebSocketDecodeError,
    WebSocketFrameTooLargeError,
    decode_client_frame,
    encode_server_frame,
)


_T = TypeVar("_T")


class WebSocketConnection(Protocol):
    async def receive_text(self, *, max_bytes: int) -> str: ...

    async def send_text(self, text: str) -> None: ...

    async def close(self, code: int, reason: str = "") -> None: ...


class _RequestFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


class _SessionFatal(Exception):
    def __init__(self, kind: str) -> None:
        self.kind = kind


class WebSocketSession:
    """One authenticated multi-run WebSocket command and event session."""

    def __init__(
        self,
        *,
        services: ChannelServices,
        authenticator: ChannelAuthenticator,
        handshake_scope: RequestScope,
        headers: HttpHeaders,
        connection: WebSocketConnection,
        buffer_limits: WebSocketBufferLimits,
        max_subscriptions: int = 32,
        max_inbound_bytes: int = 256 * 1024,
    ) -> None:
        if type(max_subscriptions) is not int or not 1 <= max_subscriptions <= 128:
            raise ValueError("max_subscriptions is invalid")
        if type(max_inbound_bytes) is not int or not 1 <= max_inbound_bytes <= 256 * 1024:
            raise ValueError("max_inbound_bytes is invalid")
        self._operations = WebSocketRunOperations(
            services,
            authenticator,
            handshake_scope,
            headers,
        )
        self._connection = connection
        self._buffer = WebSocketOutboundBuffer(buffer_limits)
        self._subscription_pump = WebSocketSubscriptionPump(
            self._buffer, lambda: self._signal_fatal("slow")
        )
        self._max_subscriptions = max_subscriptions
        self._max_inbound_bytes = max_inbound_bytes
        self._subscriptions: dict[WebSocketSubscriptionKey, WebSocketSubscriptionState] = {}
        self._generation = 0
        self._sender: asyncio.Task[None] | None = None
        self._fatal = asyncio.Event()
        self._fatal_kind = "internal"
        self._closed = False

    async def run(self) -> None:
        self._sender = asyncio.create_task(self._send_loop())
        try:
            while True:
                text = await self._receive_or_fatal()
                try:
                    await self._process_or_fatal(text)
                except WebSocketBufferOverflow:
                    await self._close_fatal("slow")
                    return
        except _SessionFatal as error:
            await self._close_fatal(error.kind)
        except WebSocketDisconnected:
            return
        except WebSocketBinaryFrame:
            await self._connection.close(1003)
        except (WebSocketFrameTooLarge, WebSocketFrameTooLargeError):
            await self._connection.close(1009)
        except WebSocketProtocolViolation:
            await self._connection.close(4400)
        except asyncio.CancelledError:
            await self._connection.close(1001)
            raise
        except BaseException:
            await self._connection.close(1011)
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_sender()
        subscriptions = tuple(self._subscriptions.items())
        await asyncio.gather(
            *(self._close_subscription(key, state, drop=False) for key, state in subscriptions),
            return_exceptions=True,
        )
        await self._buffer.aclose()

    async def _receive_or_fatal(self) -> str:
        return await self._await_or_fatal(
            self._connection.receive_text(max_bytes=self._max_inbound_bytes),
        )

    async def _process_or_fatal(self, text: str) -> None:
        await self._await_or_fatal(self._process_text(text))

    async def _await_or_fatal(self, operation: Awaitable[_T]) -> _T:
        operation_task = asyncio.ensure_future(operation)
        fatal = asyncio.create_task(self._fatal.wait())
        try:
            done, _ = await asyncio.wait(
                (operation_task, fatal),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if fatal in done and self._fatal.is_set():
                raise _SessionFatal(self._fatal_kind)
            return operation_task.result()
        finally:
            fatal.cancel()
            if not operation_task.done():
                operation_task.cancel()
            await asyncio.gather(fatal, operation_task, return_exceptions=True)

    async def _process_text(self, text: str) -> None:
        try:
            frame = decode_client_frame(text)
        except WebSocketFrameTooLargeError:
            raise
        except WebSocketDecodeError as error:
            await self._put_control(
                ErrorFrame(error.request_id, "invalid_request", "invalid request"),
            )
            return
        try:
            if type(frame) is SubmitRunFrame:
                await self._put_control(await self._operations.submit_run(frame))
            elif type(frame) is SubmitCommandFrame:
                await self._put_control(await self._operations.submit_command(frame))
            elif type(frame) is SubscribeRunFrame:
                await self._subscribe(frame)
            elif type(frame) is UnsubscribeRunFrame:
                await self._unsubscribe(frame)
            else:
                raise TypeError("client frame type is invalid")
        except _RequestFailure as error:
            await self._put_control(ErrorFrame(frame.request_id, error.code, error.message))
        except RunStreamCursorError:
            await self._put_control(
                ErrorFrame(frame.request_id, "invalid_request", "invalid request"),
            )
        except WebSocketBufferOverflow:
            raise
        except Exception as error:
            _status, code, message = map_http_error(error)
            if code == "internal_error":
                raise _SessionFatal("internal") from error
            await self._put_control(ErrorFrame(frame.request_id, code, message))

    async def _subscribe(self, frame: SubscribeRunFrame) -> None:
        scope = await self._operations.authorize_subscription(frame, "subscribe_run")
        key = (frame.session_id, frame.run_id)
        if key in self._subscriptions:
            raise _RequestFailure("subscription_exists", "subscription already exists")
        if len(self._subscriptions) >= self._max_subscriptions:
            raise _RequestFailure("subscription_limit", "subscription limit reached")
        stream = await self._operations.subscribe(
            frame,
            scope=scope,
        )
        self._generation += 1
        state = WebSocketSubscriptionState(stream, self._generation)
        self._subscriptions[key] = state
        await self._put_control(
            self._operations.subscription_receipt(frame, "subscribe_run"),
        )
        state.task = asyncio.create_task(
            self._subscription_pump.run(key, state.stream, state.generation),
        )

    async def _unsubscribe(self, frame: UnsubscribeRunFrame) -> None:
        await self._operations.authorize_subscription(frame, "unsubscribe_run")
        key = (frame.session_id, frame.run_id)
        state = self._subscriptions.get(key)
        if state is not None:
            await self._close_subscription(key, state, drop=True)
        await self._put_control(
            self._operations.subscription_receipt(frame, "unsubscribe_run"),
        )

    async def _send_loop(self) -> None:
        try:
            while True:
                frame = await self._buffer.get()
                state = (
                    None
                    if frame.subscription is None
                    else self._subscriptions.get(frame.subscription)
                )
                if frame.subscription is not None and (
                    state is None
                    or state.generation != frame.generation
                    or state.closing
                ):
                    continue
                await self._connection.send_text(frame.text)
                if state is not None and frame.cursor is not None:
                    state.last_sent_cursor = frame.cursor
                if state is not None and frame.terminal:
                    await self._close_subscription(frame.subscription, state, drop=False)
        except (asyncio.CancelledError, WebSocketBufferClosed):
            return
        except BaseException:
            self._signal_fatal("internal")

    async def _put_control(self, frame: ServerFrame) -> None:
        await self._buffer.put_control(encode_server_frame(frame))

    async def _close_subscription(
        self,
        key: WebSocketSubscriptionKey,
        state: WebSocketSubscriptionState,
        *,
        drop: bool,
    ) -> None:
        if state.closed:
            return
        state.closing = True
        state.drop_queued_frames = state.drop_queued_frames or drop
        task = state.task
        pump_stop_task = state.pump_stop_task
        if (
            pump_stop_task is None
            and task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()
            pump_stop_task = asyncio.create_task(_wait_for_task(task))
            state.pump_stop_task = pump_stop_task
        if pump_stop_task is not None:
            await asyncio.shield(pump_stop_task)
        close_task = state.close_task
        if close_task is None:
            close_task = asyncio.create_task(state.stream.aclose())
            state.close_task = close_task
        try:
            await asyncio.shield(close_task)
        finally:
            if close_task.cancelled():
                state.close_task = None
            elif close_task.done():
                state.closed = True
                if self._subscriptions.get(key) is state:
                    self._subscriptions.pop(key)
                    if state.drop_queued_frames:
                        await self._buffer.drop(key)

    async def _close_fatal(self, kind: str) -> None:
        resume = tuple(
            ResumeCursor(key[0], key[1], state.last_sent_cursor)
            for key, state in self._subscriptions.items()
        )
        await self.aclose()
        if kind == "slow":
            try:
                await self._connection.send_text(
                    encode_server_frame(
                        ErrorFrame(None, "slow_consumer", "slow consumer", resume_cursors=resume),
                    ),
                )
            except BaseException:
                pass
            await self._connection.close(4408)
        else:
            await self._connection.close(1011)

    async def _cancel_sender(self) -> None:
        sender = self._sender
        self._sender = None
        if sender is not None and sender is not asyncio.current_task() and not sender.done():
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    def _signal_fatal(self, kind: str) -> None:
        if not self._fatal.is_set():
            self._fatal_kind = kind
            self._fatal.set()


async def _wait_for_task(task: asyncio.Task[None]) -> None:
    await asyncio.gather(task, return_exceptions=True)

__all__ = ["WebSocketConnection", "WebSocketSession"]
