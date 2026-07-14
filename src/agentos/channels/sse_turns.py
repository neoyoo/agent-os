from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agentos.channels.types import ChannelTurnRequest
from agentos.runtime import Agent
from agentos.runtime.agent_stream import AgentStream


@dataclass(slots=True)
class SseTurnEntry:
    """ASGI SSE 单个 turn 的生产者生命周期。"""

    session_id: str
    turn_stream_id: str
    stream_key: str
    agent: Agent
    request: ChannelTurnRequest
    stream: AgentStream | None = None
    next_sequence: int = 1
    terminal: bool = False
    active_readers: int = 0
    released: bool = False
    closed: bool = False
    lease_error: BaseException | None = None
    turn_control_task: asyncio.Task[None] | None = None
    runner_task: asyncio.Task[None] | None = None
    lease_heartbeat_task: asyncio.Task[None] | None = None
    grace_task: asyncio.Task[None] | None = None
    gc_task: asyncio.Task[None] | None = None
    gc_handle: asyncio.TimerHandle | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
