from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import TypeAlias


WebSocketSubscriptionKey: TypeAlias = tuple[str, str]


class WebSocketBufferClosed(RuntimeError):
    """The connection buffer no longer accepts or yields frames."""


class WebSocketBufferOverflow(RuntimeError):
    """A connection or subscription queue budget would be exceeded."""


@dataclass(frozen=True, slots=True)
class WebSocketBufferLimits:
    connection_frames: int = 256
    connection_bytes: int = 4 * 1024 * 1024
    subscription_frames: int = 64
    subscription_bytes: int = 512 * 1024

    def __post_init__(self) -> None:
        values = (
            (self.connection_frames, 2048),
            (self.connection_bytes, 16 * 1024 * 1024),
            (self.subscription_frames, 512),
            (self.subscription_bytes, 2 * 1024 * 1024),
        )
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in values):
            raise ValueError("WebSocket buffer limits are invalid")
        if (
            self.connection_frames < self.subscription_frames
            or self.connection_bytes < self.subscription_bytes
        ):
            raise ValueError("WebSocket buffer limits are incoherent")


@dataclass(frozen=True, slots=True)
class BufferedWebSocketFrame:
    text: str
    size: int
    subscription: WebSocketSubscriptionKey | None = None
    cursor: str | None = None
    generation: int = 0
    terminal: bool = False


class WebSocketOutboundBuffer:
    """Bounded FIFO with connection and per-subscription accounting."""

    def __init__(self, limits: WebSocketBufferLimits) -> None:
        if type(limits) is not WebSocketBufferLimits:
            raise TypeError("limits must be WebSocketBufferLimits")
        self._limits = limits
        self._frames: deque[BufferedWebSocketFrame] = deque()
        self._subscription_frames: dict[WebSocketSubscriptionKey, int] = {}
        self._subscription_bytes: dict[WebSocketSubscriptionKey, int] = {}
        self._queued_bytes = 0
        self._closed = False
        self._condition = asyncio.Condition()

    @property
    def queued_frames(self) -> int:
        return len(self._frames)

    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    async def put_control(self, text: str) -> None:
        await self._put(text, subscription=None, cursor=None)

    async def put_event(
        self,
        subscription: WebSocketSubscriptionKey,
        text: str,
        *,
        cursor: str | None,
        generation: int = 0,
        terminal: bool = False,
    ) -> None:
        _validate_subscription(subscription)
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be non-negative")
        if type(terminal) is not bool:
            raise TypeError("terminal must be bool")
        await self._put(
            text,
            subscription=subscription,
            cursor=cursor,
            generation=generation,
            terminal=terminal,
        )

    async def get(self) -> BufferedWebSocketFrame:
        async with self._condition:
            while not self._frames:
                if self._closed:
                    raise WebSocketBufferClosed()
                await self._condition.wait()
            frame = self._frames.popleft()
            self._queued_bytes -= frame.size
            if frame.subscription is not None:
                self._decrement_subscription(frame.subscription, frame.size)
            return frame

    async def drop(self, subscription: WebSocketSubscriptionKey) -> None:
        _validate_subscription(subscription)
        async with self._condition:
            retained: deque[BufferedWebSocketFrame] = deque()
            removed_bytes = 0
            for frame in self._frames:
                if frame.subscription == subscription:
                    removed_bytes += frame.size
                else:
                    retained.append(frame)
            self._frames = retained
            self._queued_bytes -= removed_bytes
            self._subscription_frames.pop(subscription, None)
            self._subscription_bytes.pop(subscription, None)

    async def aclose(self) -> None:
        async with self._condition:
            if self._closed:
                return
            self._closed = True
            self._frames.clear()
            self._subscription_frames.clear()
            self._subscription_bytes.clear()
            self._queued_bytes = 0
            self._condition.notify_all()

    async def _put(
        self,
        text: str,
        *,
        subscription: WebSocketSubscriptionKey | None,
        cursor: str | None,
        generation: int = 0,
        terminal: bool = False,
    ) -> None:
        if type(text) is not str:
            raise TypeError("WebSocket frame text must be str")
        size = len(text.encode("utf-8"))
        async with self._condition:
            if self._closed:
                raise WebSocketBufferClosed()
            if self._over_connection_limit(size) or (
                subscription is not None and self._over_subscription_limit(subscription, size)
            ):
                raise WebSocketBufferOverflow()
            frame = BufferedWebSocketFrame(
                text,
                size,
                subscription,
                cursor,
                generation,
                terminal,
            )
            self._frames.append(frame)
            self._queued_bytes += size
            if subscription is not None:
                self._subscription_frames[subscription] = (
                    self._subscription_frames.get(subscription, 0) + 1
                )
                self._subscription_bytes[subscription] = (
                    self._subscription_bytes.get(subscription, 0) + size
                )
            self._condition.notify()

    def _over_connection_limit(self, size: int) -> bool:
        return (
            len(self._frames) + 1 > self._limits.connection_frames
            or self._queued_bytes + size > self._limits.connection_bytes
        )

    def _over_subscription_limit(
        self,
        subscription: WebSocketSubscriptionKey,
        size: int,
    ) -> bool:
        return (
            self._subscription_frames.get(subscription, 0) + 1
            > self._limits.subscription_frames
            or self._subscription_bytes.get(subscription, 0) + size
            > self._limits.subscription_bytes
        )

    def _decrement_subscription(
        self,
        subscription: WebSocketSubscriptionKey,
        size: int,
    ) -> None:
        frames = self._subscription_frames[subscription] - 1
        queued_bytes = self._subscription_bytes[subscription] - size
        if frames == 0:
            self._subscription_frames.pop(subscription)
            self._subscription_bytes.pop(subscription)
        else:
            self._subscription_frames[subscription] = frames
            self._subscription_bytes[subscription] = queued_bytes


def _validate_subscription(subscription: object) -> None:
    if (
        type(subscription) is not tuple
        or len(subscription) != 2
        or any(type(value) is not str or not value for value in subscription)
    ):
        raise TypeError("subscription must be a session/run tuple")


__all__ = [
    "BufferedWebSocketFrame",
    "WebSocketBufferClosed",
    "WebSocketBufferLimits",
    "WebSocketBufferOverflow",
    "WebSocketOutboundBuffer",
    "WebSocketSubscriptionKey",
]
