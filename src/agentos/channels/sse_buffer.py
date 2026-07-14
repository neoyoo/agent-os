from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from agentos.channels._redis_sse_sync import RedisSseSyncClient


@dataclass(frozen=True, slots=True)
class SseReplayWindow:
    """Observable replay window for a buffered SSE stream."""

    exists: bool
    terminal: bool
    first_sequence: int | None
    last_sequence: int | None

    def has_gap_after(self, last_sequence: int) -> bool:
        """Return True when required events have fallen out of the buffer."""

        return self.first_sequence is not None and self.first_sequence > last_sequence + 1


class SseEventBuffer(Protocol):
    """SSE 事件 replay + tail 边界。"""

    async def append(self, stream_key: str, sequence: int, chunk: str) -> None:
        """追加一个事件 chunk。"""

    async def replay_since(self, stream_key: str, last_sequence: int) -> list[tuple[int, str]]:
        """返回 sequence 大于 last_sequence 的已缓存事件。"""

    async def replay_window(self, stream_key: str) -> SseReplayWindow:
        """Describe retained sequence bounds without creating a missing stream."""

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

    async def replay_window(self, stream_key: str) -> SseReplayWindow:
        stream = self._streams.get(stream_key)
        if stream is None:
            return SseReplayWindow(
                exists=False,
                terminal=False,
                first_sequence=None,
                last_sequence=None,
            )
        async with stream.condition:
            sequences = [sequence for sequence, _chunk in stream.events]
            return SseReplayWindow(
                exists=True,
                terminal=stream.terminal,
                first_sequence=sequences[0] if sequences else None,
                last_sequence=sequences[-1] if sequences else None,
            )

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


class RedisSseEventBuffer:
    """Redis Streams-backed SSE event buffer。"""

    def __init__(
        self,
        url: str,
        client: object | None = None,
        *,
        key_prefix: str = "agentos",
        max_stream_length: int = 512,
        ttl_seconds: int = 300,
        xread_block_ms: int = 1000,
    ) -> None:
        """创建 Redis SSE buffer；未安装 redis extra 时给出清晰错误。"""

        if max_stream_length < 1:
            raise ValueError("max_stream_length must be at least 1")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be at least 1")
        if xread_block_ms < 1:
            raise ValueError("xread_block_ms must be at least 1")
        if client is not None:
            redis_client = client
            self._url = url
        else:
            try:
                import redis
            except ImportError as error:
                raise RuntimeError(
                    "RedisSseEventBuffer requires the optional dependency "
                    "`agentos[redis]`.",
                ) from error
            redis_client = redis.Redis.from_url(url)
            self._url = url
        self._key_prefix = key_prefix.rstrip(":")
        self._max_stream_length = max_stream_length
        self._ttl_seconds = ttl_seconds
        self._xread_block_ms = xread_block_ms
        self._redis = RedisSseSyncClient(redis_client)

    @property
    def backend_url(self) -> str:
        """返回 Redis backend URL。"""

        return self._url

    async def append(self, stream_key: str, sequence: int, chunk: str) -> None:
        """追加一个事件 chunk。"""

        key = self._redis_key(stream_key)
        await self._redis.mutate(
            key,
            "xadd",
            key,
            {"type": "event", "sequence": str(sequence), "chunk": chunk},
            maxlen=self._max_stream_length,
            approximate=True,
        )
        await self._expire(key)

    async def replay_since(self, stream_key: str, last_sequence: int) -> list[tuple[int, str]]:
        """返回 sequence 大于 last_sequence 的已缓存事件。"""

        key = self._redis_key(stream_key)
        messages = await self._read_stream(key)
        events: list[tuple[int, str]] = []
        for _message_id, fields in messages:
            parsed = self._parse_event_fields(fields)
            if parsed is None:
                continue
            sequence, chunk = parsed
            if sequence > last_sequence:
                events.append((sequence, chunk))
        return events

    async def replay_window(self, stream_key: str) -> SseReplayWindow:
        key = self._redis_key(stream_key)
        messages = await self._read_stream(key)
        if not messages:
            return SseReplayWindow(
                exists=False,
                terminal=False,
                first_sequence=None,
                last_sequence=None,
            )
        sequences: list[int] = []
        terminal = False
        for _message_id, fields in messages:
            if self._field(fields, "type") == "terminal":
                terminal = True
                continue
            parsed = self._parse_event_fields(fields)
            if parsed is not None:
                sequences.append(parsed[0])
        return SseReplayWindow(
            exists=True,
            terminal=terminal,
            first_sequence=sequences[0] if sequences else None,
            last_sequence=sequences[-1] if sequences else None,
        )

    async def follow(
        self,
        stream_key: str,
        since_sequence: int,
    ) -> AsyncIterator[tuple[int, str]]:
        """持续读取 sequence 大于 since_sequence 的事件，terminal 后结束。"""

        key = self._redis_key(stream_key)
        last_stream_id = "0-0"
        messages = await self._read_stream(key)
        for message_id, fields in messages:
            last_stream_id = self._message_id(message_id)
            if self._field(fields, "type") == "terminal":
                return
            parsed = self._parse_event_fields(fields)
            if parsed is None:
                continue
            sequence, chunk = parsed
            if sequence > since_sequence:
                since_sequence = sequence
                yield sequence, chunk

        while True:
            raw_streams = await self._redis.call(
                "xread",
                {key: last_stream_id},
                count=100,
                block=self._xread_block_ms,
            )
            if not raw_streams:
                await asyncio.sleep(0)
                continue
            for _stream_name, stream_messages in raw_streams:
                for message_id, fields in stream_messages:
                    last_stream_id = self._message_id(message_id)
                    if self._field(fields, "type") == "terminal":
                        return
                    parsed = self._parse_event_fields(fields)
                    if parsed is None:
                        continue
                    sequence, chunk = parsed
                    if sequence > since_sequence:
                        since_sequence = sequence
                        yield sequence, chunk

    async def mark_terminal(self, stream_key: str) -> None:
        """标记 stream 已结束并唤醒 follower。"""

        key = self._redis_key(stream_key)
        await self._redis.mutate(
            key,
            "xadd",
            key,
            {"type": "terminal"},
        )
        await self._expire(key)

    async def drop(self, stream_key: str) -> None:
        """删除 stream 缓存。"""

        key = self._redis_key(stream_key)
        await self._redis.delete(key)

    async def _expire(self, key: str) -> None:
        await self._redis.expire(key, self._ttl_seconds)

    async def _read_stream(self, key: str) -> object:
        """读取当前 Redis stream 快照。"""
        return await self._redis.call("xrange", key, min="-", max="+")

    def _redis_key(self, stream_key: str) -> str:
        return f"{self._key_prefix}:channels:sse:{stream_key}"

    def _parse_event_fields(self, fields: object) -> tuple[int, str] | None:
        if self._field(fields, "type") != "event":
            return None
        sequence = self._field(fields, "sequence")
        chunk = self._field(fields, "chunk")
        if sequence is None or chunk is None:
            return None
        return int(sequence), chunk

    def _field(self, fields: object, name: str) -> str | None:
        if not isinstance(fields, dict):
            return None
        value = fields.get(name)
        if value is None:
            value = fields.get(name.encode("utf-8"))
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _message_id(self, value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)
