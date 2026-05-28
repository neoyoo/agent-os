from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


class SseEventBuffer(Protocol):
    """SSE 事件 replay + tail 边界。"""

    async def append(self, stream_key: str, sequence: int, chunk: str) -> None:
        """追加一个事件 chunk。"""

    async def replay_since(self, stream_key: str, last_sequence: int) -> list[tuple[int, str]]:
        """返回 sequence 大于 last_sequence 的已缓存事件。"""

    async def follow(
        self,
        stream_key: str,
        since_sequence: int,
    ) -> AsyncIterator[tuple[int, str]]:
        """持续读取 sequence 大于 since_sequence 的事件，terminal 后结束。"""

    async def mark_terminal(self, stream_key: str) -> None:
        """标记 stream 已结束并唤醒 follower。"""

    async def drop(self, stream_key: str) -> None:
        """删除 stream 缓存。"""


@dataclass(slots=True)
class _MemoryStream:
    events: deque[tuple[int, str]]
    terminal: bool = False
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class InMemorySseEventBuffer:
    """单进程 SSE event buffer。"""

    def __init__(self, max_events_per_stream: int = 512) -> None:
        """创建内存 buffer。"""

        if max_events_per_stream < 1:
            raise ValueError("max_events_per_stream must be at least 1")
        self._max_events_per_stream = max_events_per_stream
        self._streams: dict[str, _MemoryStream] = {}

    async def append(self, stream_key: str, sequence: int, chunk: str) -> None:
        """追加一个事件 chunk。"""

        stream = self._stream(stream_key)
        async with stream.condition:
            stream.events.append((sequence, chunk))
            while len(stream.events) > self._max_events_per_stream:
                stream.events.popleft()
            stream.condition.notify_all()

    async def replay_since(self, stream_key: str, last_sequence: int) -> list[tuple[int, str]]:
        """返回 sequence 大于 last_sequence 的已缓存事件。"""

        stream = self._streams.get(stream_key)
        if stream is None:
            return []
        async with stream.condition:
            return [
                (sequence, chunk)
                for sequence, chunk in stream.events
                if sequence > last_sequence
            ]

    async def follow(
        self,
        stream_key: str,
        since_sequence: int,
    ) -> AsyncIterator[tuple[int, str]]:
        """持续读取 sequence 大于 since_sequence 的事件，terminal 后结束。"""

        stream = self._stream(stream_key)
        last_sequence = since_sequence
        while True:
            async with stream.condition:
                while True:
                    pending = [
                        (sequence, chunk)
                        for sequence, chunk in stream.events
                        if sequence > last_sequence
                    ]
                    if pending:
                        break
                    if stream.terminal:
                        return
                    await stream.condition.wait()
            for sequence, chunk in pending:
                last_sequence = sequence
                yield sequence, chunk

    async def mark_terminal(self, stream_key: str) -> None:
        """标记 stream 已结束并唤醒 follower。"""

        stream = self._stream(stream_key)
        async with stream.condition:
            stream.terminal = True
            stream.condition.notify_all()

    async def drop(self, stream_key: str) -> None:
        """删除 stream 缓存。"""

        stream = self._streams.pop(stream_key, None)
        if stream is None:
            return
        async with stream.condition:
            stream.terminal = True
            stream.condition.notify_all()

    def _stream(self, stream_key: str) -> _MemoryStream:
        stream = self._streams.get(stream_key)
        if stream is None:
            stream = _MemoryStream(deque())
            self._streams[stream_key] = stream
        return stream
