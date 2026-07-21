from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from agentos.channels.websocket_buffer import (
    WebSocketBufferOverflow,
    WebSocketOutboundBuffer,
    WebSocketSubscriptionKey,
)
from agentos.distributed.models import StreamGap
from agentos.distributed.protocols import EventSubscription
from agentos.transports.run_stream import (
    is_terminal_event,
    project_replay_item,
    project_stream_gap_projection,
)
from agentos.transports.websocket import (
    ErrorFrame,
    EventFrame,
    StreamGapFrame,
    encode_server_frame,
)


@dataclass(slots=True)
class WebSocketSubscriptionState:
    stream: EventSubscription
    generation: int
    task: asyncio.Task[None] | None = None
    pump_stop_task: asyncio.Task[None] | None = None
    close_task: asyncio.Task[None] | None = None
    last_sent_cursor: str | None = None
    closing: bool = False
    closed: bool = False
    drop_queued_frames: bool = False


class WebSocketSubscriptionPump:
    """Projects one replay stream into the connection's bounded buffer."""

    def __init__(
        self,
        buffer: WebSocketOutboundBuffer,
        signal_slow_consumer: Callable[[], None],
    ) -> None:
        self._buffer = buffer
        self._signal_slow_consumer = signal_slow_consumer

    async def run(
        self,
        key: WebSocketSubscriptionKey,
        stream: EventSubscription,
        generation: int,
    ) -> None:
        try:
            async for item in stream:
                if type(item) is StreamGap:
                    await self._put_gap(key, item, generation)
                    return
                projection = project_replay_item(item)
                terminal = is_terminal_event(item.event.event)
                await self._buffer.put_event(
                    key,
                    encode_server_frame(EventFrame(*key, projection)),
                    cursor=projection.cursor,
                    generation=generation,
                    terminal=terminal,
                )
                if terminal:
                    return
            await self._put_error(key, generation)
        except asyncio.CancelledError:
            raise
        except WebSocketBufferOverflow:
            self._signal_slow_consumer()
        except Exception:
            try:
                await self._put_error(key, generation)
            except WebSocketBufferOverflow:
                self._signal_slow_consumer()

    async def _put_gap(
        self,
        key: WebSocketSubscriptionKey,
        gap: StreamGap,
        generation: int,
    ) -> None:
        await self._buffer.put_event(
            key,
            encode_server_frame(
                StreamGapFrame(*key, project_stream_gap_projection(gap)),
            ),
            cursor=None,
            generation=generation,
            terminal=True,
        )

    async def _put_error(
        self,
        key: WebSocketSubscriptionKey,
        generation: int,
    ) -> None:
        await self._buffer.put_event(
            key,
            encode_server_frame(
                ErrorFrame(
                    None,
                    "stream_unavailable",
                    "stream unavailable",
                    session_id=key[0],
                    run_id=key[1],
                ),
            ),
            cursor=None,
            generation=generation,
            terminal=True,
        )


__all__ = ["WebSocketSubscriptionPump", "WebSocketSubscriptionState"]
