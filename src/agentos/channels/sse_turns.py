from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agentos.channels.types import ChannelTurnRequest
from agentos.runtime import Agent


@dataclass(slots=True)
class SseTurnEntry:
    """ASGI SSE 单个 turn 的生产者生命周期。"""

    session_id: str
    turn_stream_id: str
    stream_key: str
    agent: Agent
    request: ChannelTurnRequest
    next_sequence: int = 1
    terminal: bool = False
    active_readers: int = 0
    released: bool = False
    closed: bool = False
    runner_task: asyncio.Task[None] | None = None
    grace_task: asyncio.Task[None] | None = None
    gc_task: asyncio.Task[None] | None = None
    gc_handle: asyncio.TimerHandle | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
