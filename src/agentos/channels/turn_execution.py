from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast

from agentos.channels._sync_adapter import acquire_sync_resource, run_sync_call
from agentos.channels.session import AgentSessionProvider, AsyncAgentSessionProvider
from agentos.runtime import Agent, RunInput, RunOptions, RunOutcome
from agentos.runtime.agent_stream import AgentStream


SessionProvider = AgentSessionProvider | AsyncAgentSessionProvider


async def acquire_channel_agent(
    sessions: SessionProvider,
    session_id: str,
) -> Agent:
    """Acquire a session Agent without blocking the channel event loop."""

    if isinstance(sessions, AsyncAgentSessionProvider):
        return await sessions.async_get_agent(session_id)
    provider = cast(AgentSessionProvider, sessions)
    return await acquire_sync_resource(provider.get_agent, provider.release_agent, session_id)


async def cancel_and_join_task(task: asyncio.Task[None] | None) -> None:
    """Cancel a channel lifecycle task and wait for its cleanup to finish."""

    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise


async def release_channel_agent(
    sessions: SessionProvider,
    session_id: str,
    agent: Agent,
) -> None:
    """Release a session Agent after all run-owned resources are closed."""

    if isinstance(sessions, AsyncAgentSessionProvider):
        await sessions.async_release_agent(session_id, agent)
        return
    provider = cast(AgentSessionProvider, sessions)
    await run_sync_call(provider.release_agent, session_id, agent)


async def run_channel_agent(
    sessions: SessionProvider,
    session_id: str,
    input: RunInput,
    *,
    options: RunOptions | None = None,
) -> RunOutcome:
    """Run one non-streaming turn and release the acquired session."""

    agent = await acquire_channel_agent(sessions, session_id)
    try:
        return await agent.run(input, options=options)
    finally:
        await release_channel_agent(sessions, session_id, agent)


@asynccontextmanager
async def open_channel_agent_stream(
    sessions: SessionProvider,
    session_id: str,
    input: RunInput,
    *,
    options: RunOptions | None = None,
    on_agent: Callable[[Agent], None] | None = None,
) -> AsyncIterator[AgentStream]:
    """Open a run stream and release its session after stream cleanup."""

    agent = await acquire_channel_agent(sessions, session_id)
    try:
        if on_agent is not None:
            on_agent(agent)
        stream = await agent.run(input, stream=True, options=options)
        async with stream:
            yield stream
    finally:
        await release_channel_agent(sessions, session_id, agent)
